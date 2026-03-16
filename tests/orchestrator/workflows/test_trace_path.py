"""Tests for the trace_attack_path workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from q_ai.core.models import RunStatus
from q_ai.orchestrator.workflows.trace_path import trace_attack_path

_CHAIN_PATCH = "q_ai.orchestrator.workflows.trace_path.ChainAdapter"


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


def _base_config() -> dict:
    """Create a minimal valid config."""
    return {
        "target_id": "target-1",
        "chain_file": "/path/to/chain.yaml",
        "transport": "stdio",
        "command": "echo hi",
        "url": None,
        "inject_model": "openai/gpt-4",
    }


class TestTracePathWorkflow:
    """Tests for the trace_attack_path workflow executor."""

    async def test_chain_success(self) -> None:
        """result.success=True -> COMPLETED."""
        runner = _make_runner()
        config = _base_config()

        mock_result = MagicMock()
        mock_result.success = True

        with patch(_CHAIN_PATCH) as MockChain:
            MockChain.return_value.run = AsyncMock(return_value=mock_result)
            await trace_attack_path(runner, config)

        MockChain.assert_called_once_with(runner, config)
        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_chain_failure(self) -> None:
        """result.success=False -> FAILED."""
        runner = _make_runner()
        config = _base_config()

        mock_result = MagicMock()
        mock_result.success = False

        with patch(_CHAIN_PATCH) as MockChain:
            MockChain.return_value.run = AsyncMock(return_value=mock_result)
            await trace_attack_path(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.FAILED)

    async def test_chain_adapter_raises(self) -> None:
        """Adapter raises -> FAILED, exception re-raised."""
        runner = _make_runner()
        config = _base_config()

        with patch(_CHAIN_PATCH) as MockChain:
            MockChain.return_value.run = AsyncMock(side_effect=RuntimeError("chain error"))
            with pytest.raises(RuntimeError, match="chain error"):
                await trace_attack_path(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.FAILED)
