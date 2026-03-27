"""Adapter for running chain executions through the orchestrator.

Wraps execute_chain(), handling child run lifecycle, DB persistence,
and event emission. Error handling: fail_fast (D6).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.chain.executor import execute_chain
from q_ai.chain.executor_models import TargetConfig
from q_ai.chain.loader import ChainValidationError, load_chain
from q_ai.chain.mapper import persist_chain
from q_ai.chain.models import ChainResult
from q_ai.core.models import RunStatus, Severity

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner


@dataclass
class ChainAdapterResult:
    """Result from a chain adapter run."""

    run_id: str
    chain_result: ChainResult
    step_count: int
    success: bool


class ChainAdapter:
    """Adapter for running chain executions through the orchestrator.

    Wraps execute_chain(), handling child run lifecycle, DB persistence,
    and event emission. Uses fail_fast error handling (D6).
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the chain adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: chain_file, transport,
                command, url, inject_model, target_id.
        """
        self._runner = runner
        self._config = config

    async def run(self) -> ChainAdapterResult:
        """Execute a chain within the orchestrator lifecycle.

        Creates a child run, loads and validates the chain, builds a
        TargetConfig, executes the chain, persists results, and emits events.

        Returns:
            ChainAdapterResult with run_id, chain_result, step_count, success.
        """
        child_id = await self._runner.create_child_run("chain")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            await self._runner.emit_progress(child_id, "Loading chain...")

            try:
                chain = load_chain(Path(self._config["chain_file"]))
            except ChainValidationError:
                await self._runner.update_child_status(child_id, RunStatus.FAILED)
                raise

            audit_command = (
                shlex.split(self._config["command"]) if self._config.get("command") else None
            )
            target_config = TargetConfig(
                audit_transport=self._config["transport"],
                audit_command=audit_command,
                audit_url=self._config.get("url"),
                inject_model=self._config["inject_model"],
                ipi_callback_url=self._config.get("ipi_callback_url"),
                ipi_output_dir=self._config.get("ipi_output_dir"),
                ipi_format=self._config.get("ipi_format"),
                cxp_format_id=self._config.get("cxp_format_id"),
                cxp_output_dir=self._config.get("cxp_output_dir"),
                cxp_rule_ids=self._config.get("cxp_rule_ids"),
                rxp_model_id=self._config.get("rxp_model_id"),
                rxp_profile_id=self._config.get("rxp_profile_id"),
                rxp_top_k=self._config.get("rxp_top_k"),
            )

            await self._runner.emit_progress(
                child_id,
                f"Executing chain: {chain.name} ({len(chain.steps)} steps)",
            )

            async def _gate_callback(step_id: str, message: str) -> None:
                await self._runner.wait_for_user(message)

            chain_result = await execute_chain(chain, target_config, gate_callback=_gate_callback)

            persist_chain(
                chain_result,
                chain,
                db_path=self._runner._db_path,
                run_id=child_id,
            )

            # Emit finding for each failed step
            for step_output in chain_result.step_outputs:
                if not getattr(step_output, "success", True):
                    await self._runner.emit_finding(
                        finding_id=f"{chain.id}-{step_output.step_id}",
                        run_id=child_id,
                        module="chain",
                        severity=int(Severity.HIGH),
                        title=f"Chain step failed: {step_output.step_id}",
                    )

            final_status = RunStatus.COMPLETED if chain_result.success else RunStatus.FAILED
            await self._runner.update_child_status(child_id, final_status)

            return ChainAdapterResult(
                run_id=child_id,
                chain_result=chain_result,
                step_count=len(chain_result.step_outputs),
                success=chain_result.success,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
