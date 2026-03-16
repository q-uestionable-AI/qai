"""Tests for the test_coding_assistant workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import RunStatus
from q_ai.orchestrator.workflows.test_assistant import (
    test_coding_assistant as _test_coding_assistant,
)

_CXP_PATCH = "q_ai.orchestrator.workflows.test_assistant.CXPAdapter"


def _make_runner(run_id: str = "run-1") -> MagicMock:
    """Create a mock WorkflowRunner."""
    runner = MagicMock()
    runner.run_id = run_id
    runner._db_path = None
    runner.resolve_target = AsyncMock(return_value=MagicMock(id="target-1"))
    runner.emit_progress = AsyncMock()
    runner.complete = AsyncMock()
    runner.fail = AsyncMock()
    return runner


def _base_config(tmp_path: Path) -> dict:
    """Create a minimal valid config."""
    return {
        "target_id": "target-1",
        "format_id": "python",
        "rule_ids": None,
        "output_dir": str(tmp_path / "cxp-out"),
        "repo_name": None,
    }


class TestTestAssistantWorkflow:
    """Tests for the test_coding_assistant workflow executor."""

    async def test_cxp_success(self, tmp_path: Path) -> None:
        """CXPAdapter.run() succeeds -> COMPLETED."""
        runner = _make_runner()
        config = _base_config(tmp_path)

        with patch(_CXP_PATCH) as MockCXP:
            MockCXP.return_value.run = AsyncMock()
            await _test_coding_assistant(runner, config)

        MockCXP.assert_called_once_with(runner, config)
        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_cxp_failure(self, tmp_path: Path) -> None:
        """CXPAdapter.run() raises -> FAILED."""
        runner = _make_runner()
        config = _base_config(tmp_path)

        with patch(_CXP_PATCH) as MockCXP:
            MockCXP.return_value.run = AsyncMock(side_effect=RuntimeError("cxp fail"))
            await _test_coding_assistant(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.FAILED)
