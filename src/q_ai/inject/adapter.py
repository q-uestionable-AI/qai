"""Adapter for running inject campaigns through the orchestrator.

Wraps run_campaign(), handling child run lifecycle, DB persistence,
and event emission.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus
from q_ai.inject.campaign import run_campaign
from q_ai.inject.coverage import build_coverage_report
from q_ai.inject.mapper import _OUTCOME_SEVERITY, persist_campaign
from q_ai.inject.models import Campaign, CoverageReport, InjectionTechnique, PayloadTemplate
from q_ai.inject.payloads.loader import filter_templates, load_all_templates
from q_ai.services import finding_service

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


@dataclass
class InjectResult:
    """Result from an inject adapter run."""

    run_id: str
    campaign: Campaign
    finding_count: int
    coverage: CoverageReport | None = None


class InjectAdapter:
    """Adapter for running inject campaigns through the orchestrator.

    Wraps run_campaign(), handling child run lifecycle, DB persistence,
    and event emission.
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the inject adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: model, payloads, rounds.
        """
        self._runner = runner
        self._config = config

    async def run(self) -> InjectResult:
        """Execute an inject campaign within the orchestrator lifecycle.

        Creates a child run, loads payloads, queries audit findings for
        priority ordering, runs the campaign, persists results, and emits events.

        Returns:
            InjectResult with run_id, campaign, finding_count, and coverage.
        """
        child_id = await self._runner.create_child_run("inject")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            templates = self._select_templates()

            audit_categories = self._query_audit_categories()
            if audit_categories:
                templates = self._prioritize_by_findings(templates, audit_categories)

            await self._runner.emit_progress(child_id, f"Testing {len(templates)} payloads...")

            campaign = await run_campaign(
                templates,
                model=self._config["model"],
                rounds=self._config.get("rounds", 1),
            )

            finding_count = sum(1 for r in campaign.results if r.outcome in _OUTCOME_SEVERITY)
            coverage = (
                build_coverage_report(audit_categories, campaign, templates)
                if audit_categories
                else None
            )

            await self._runner.emit_progress(
                child_id,
                f"Campaign complete: {len(campaign.results)} results, {finding_count} findings",
            )

            persist_campaign(campaign, db_path=self._runner._db_path, run_id=child_id)
            await self._emit_findings(child_id, campaign)

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return InjectResult(
                run_id=child_id,
                campaign=campaign,
                finding_count=finding_count,
                coverage=coverage,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise

    def _select_templates(self) -> list[PayloadTemplate]:
        """Load and filter templates based on config.

        Returns:
            Filtered list of PayloadTemplate objects.
        """
        templates = load_all_templates()

        payload_names = self._config.get("payloads")
        if payload_names:
            name_set = set(payload_names)
            return [t for t in templates if t.name in name_set]

        if "techniques" not in self._config:
            return templates

        technique_strs = self._config["techniques"] or []
        filtered: list[PayloadTemplate] = []
        seen_names: set[str] = set()
        for tech_str in technique_strs:
            try:
                tech = InjectionTechnique(tech_str)
            except ValueError:
                logger.warning("Skipping unknown technique: %s", tech_str)
                continue
            for t in filter_templates(templates, technique=tech):
                if t.name not in seen_names:
                    filtered.append(t)
                    seen_names.add(t.name)
        return filtered

    async def _emit_findings(self, child_id: str, campaign: Campaign) -> None:
        """Emit finding events for security-relevant outcomes.

        Args:
            child_id: Child run ID for the inject run.
            campaign: Completed campaign with results.
        """
        for result in campaign.results:
            severity = _OUTCOME_SEVERITY.get(result.outcome)
            if severity is not None:
                await self._runner.emit_finding(
                    finding_id=f"{campaign.id}-{result.payload_name}",
                    run_id=child_id,
                    module="inject",
                    severity=int(severity),
                    title=f"{result.technique}: {result.payload_name}",
                )

    def _query_audit_categories(self) -> set[str]:
        """Query audit finding categories from the DB for the parent run.

        Returns:
            Set of unique finding categories, or empty set if unavailable.
        """
        try:
            with get_connection(self._runner._db_path) as conn:
                findings = finding_service.get_findings_for_run(conn, self._runner.run_id)
            return {f.category for f in findings if f.category}
        except Exception:
            logger.debug("No audit findings available for priority ordering", exc_info=True)
            return set()

    @staticmethod
    def _prioritize_by_findings(
        templates: list[PayloadTemplate],
        categories: set[str],
    ) -> list[PayloadTemplate]:
        """Reorder templates so those matching audit categories come first.

        All templates are preserved — this is priority ordering, not exclusion.

        Args:
            templates: Full list of templates to reorder.
            categories: Audit finding categories to match against.

        Returns:
            Reordered template list: matching templates first, then the rest.
        """
        matching = filter_templates(templates, categories=categories)
        matching_names = {t.name for t in matching}
        remaining = [t for t in templates if t.name not in matching_names]
        return matching + remaining
