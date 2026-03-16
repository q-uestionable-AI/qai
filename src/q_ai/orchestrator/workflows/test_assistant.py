"""Test a Coding Assistant workflow executor.

Builds a poisoned context repository via CXP and guides the operator
through manual testing with a coding assistant.

Config shape::

    {
        "target_id": str,
        "format_id": str,
        "rule_ids": list[str] | None,   # None = all catalog rules
        "output_dir": str,
        "repo_name": str | None,
    }
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from q_ai.core.models import RunStatus
from q_ai.cxp.adapter import CXPAdapter

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


async def test_coding_assistant(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Orchestrate CXP poisoned context repo build.

    Single-stage, no fallback. CXPAdapter already calls wait_for_user
    internally — do not add a second wait.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict — see module docstring for shape.
    """
    try:
        await runner.emit_progress(runner.run_id, "Starting CXP build...")
        await CXPAdapter(runner, config).run()
        await runner.emit_progress(runner.run_id, "CXP build complete")
        await runner.complete(RunStatus.COMPLETED)
    except Exception:
        logger.exception("CXP stage failed for target %s", config["target_id"])
        await runner.emit_progress(runner.run_id, "CXP stage failed")
        await runner.complete(RunStatus.FAILED)
