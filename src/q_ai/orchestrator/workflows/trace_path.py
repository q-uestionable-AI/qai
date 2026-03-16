"""Trace an Attack Path workflow executor.

Executes a multi-step attack chain against a real target, recording
step-by-step evidence of what succeeded and what trust boundaries
were crossed.

Config shape::

    {
        "target_id": str,
        "chain_file": str,       # absolute path to chain YAML
        "transport": str,
        "command": str | None,
        "url": str | None,
        "inject_model": str,     # provider/model format
    }
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from q_ai.chain.adapter import ChainAdapter
from q_ai.core.models import RunStatus

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


async def trace_attack_path(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Execute a chain and record results. Fail-fast on any step failure.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict — see module docstring for shape.
    """
    try:
        await runner.emit_progress(runner.run_id, "Starting chain execution...")
        result = await ChainAdapter(runner, config).run()
        if result.success:
            await runner.emit_progress(runner.run_id, "Chain execution completed successfully")
            await runner.complete(RunStatus.COMPLETED)
        else:
            await runner.emit_progress(runner.run_id, "Chain execution failed")
            await runner.complete(RunStatus.FAILED)
    except Exception:
        logger.exception("Chain adapter raised for target %s", config.get("target_id", "unknown"))
        await runner.emit_progress(runner.run_id, "Chain adapter error")
        await runner.complete(RunStatus.FAILED)
        raise
