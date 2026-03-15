"""Adapter for running inject campaigns through the orchestrator.

Wraps run_campaign(), handling child run lifecycle, DB persistence,
and event emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from q_ai.core.models import RunStatus
from q_ai.inject.campaign import run_campaign
from q_ai.inject.mapper import _OUTCOME_SEVERITY, persist_campaign
from q_ai.inject.models import Campaign
from q_ai.inject.payloads.loader import load_all_templates

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner


@dataclass
class InjectResult:
    """Result from an inject adapter run."""

    run_id: str
    campaign: Campaign
    finding_count: int


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
            config: Configuration dict with keys: model, payloads, rounds,
                audit_findings.
        """
        self._runner = runner
        self._config = config

    async def run(self) -> InjectResult:
        """Execute an inject campaign within the orchestrator lifecycle.

        Creates a child run, loads payloads, runs the campaign,
        persists results, and emits events.

        Returns:
            InjectResult with run_id, campaign, and finding_count.
        """
        child_id = await self._runner.create_child_run("inject")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            # Load payload templates
            templates = load_all_templates()

            # Filter by payload names if specified
            payload_names = self._config.get("payloads")
            if payload_names:
                name_set = set(payload_names)
                templates = [t for t in templates if t.name in name_set]

            total = len(templates)
            await self._runner.emit_progress(child_id, f"Testing {total} payloads...")

            campaign = await run_campaign(
                templates,
                model=self._config["model"],
                rounds=self._config.get("rounds", 1),
            )

            # Count security-relevant findings
            finding_count = sum(1 for r in campaign.results if r.outcome in _OUTCOME_SEVERITY)

            await self._runner.emit_progress(
                child_id,
                f"Campaign complete: {len(campaign.results)} results, {finding_count} findings",
            )

            # Persist via mapper — pass child run_id to skip run creation
            persist_campaign(
                campaign,
                db_path=self._runner._db_path,
                run_id=child_id,
            )

            # Emit finding events for security-relevant outcomes
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

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return InjectResult(
                run_id=child_id,
                campaign=campaign,
                finding_count=finding_count,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
