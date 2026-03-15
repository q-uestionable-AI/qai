"""Adapter for running CXP context file builds through the orchestrator.

Wraps the CXP builder, handling child run lifecycle, DB persistence,
human-in-the-loop waiting, and event emission. Error handling: best_effort (D6).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.models import RunStatus
from q_ai.cxp.builder import build
from q_ai.cxp.catalog import get_rule, load_catalog
from q_ai.cxp.mapper import persist_build
from q_ai.cxp.models import BuildResult, Rule

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


@dataclass
class CXPAdapterResult:
    """Result from a CXP adapter run."""

    run_id: str
    build_result: BuildResult
    rules_inserted: list[str]
    resumed: bool


class CXPAdapter:
    """Adapter for running CXP context file builds through the orchestrator.

    Wraps the CXP builder, handling child run lifecycle, DB persistence,
    human-in-the-loop waiting, and event emission. Uses best_effort error
    handling (D6).
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the CXP adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: format_id, rule_ids,
                output_dir, repo_name, target_id.
        """
        self._runner = runner
        self._config = config

    async def run(self) -> CXPAdapterResult:
        """Execute a CXP build within the orchestrator lifecycle.

        Creates a child run, loads rules, builds the poisoned repo, persists
        results, waits for user to test, then completes.

        Returns:
            CXPAdapterResult with run_id, build_result, rules_inserted, resumed.
        """
        child_id = await self._runner.create_child_run("cxp")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            await self._runner.emit_progress(child_id, "Loading CXP rules...")

            # Load rules
            rule_ids = self._config.get("rule_ids")
            if rule_ids is not None:
                rules: list[Rule] = []
                for rid in rule_ids:
                    rule = get_rule(rid)
                    if rule is None:
                        logger.warning("CXP rule '%s' not found, skipping", rid)
                        continue
                    rules.append(rule)
            else:
                rules = load_catalog()

            format_id = self._config["format_id"]
            repo_name = self._config.get("repo_name") or f"cxp-{format_id}"
            output_dir = Path(self._config["output_dir"])

            build_result = await asyncio.to_thread(build, format_id, rules, output_dir, repo_name)

            persist_build(
                format_id,
                build_result.rules_inserted,
                str(build_result.repo_dir),
                db_path=self._runner._db_path,
                run_id=child_id,
            )

            await self._runner.emit_progress(
                child_id,
                f"Repo built at {build_result.repo_dir}",
            )

            await self._runner.update_child_status(child_id, RunStatus.WAITING_FOR_USER)
            await self._runner.wait_for_user(
                f"Open the repo at {build_result.repo_dir} in your coding assistant "
                "and run the trigger prompt from prompt-reference.md, then click Resume."
            )
            await self._runner.update_child_status(child_id, RunStatus.RUNNING)

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return CXPAdapterResult(
                run_id=child_id,
                build_result=build_result,
                rules_inserted=build_result.rules_inserted,
                resumed=True,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
