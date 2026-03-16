"""Tests for the measure_blast_radius workflow."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import RunStatus
from q_ai.orchestrator.workflows.blast_radius import measure_blast_radius

_GET_CONN_PATCH = "q_ai.orchestrator.workflows.blast_radius.get_connection"
_ANALYZE_PATCH = "q_ai.orchestrator.workflows.blast_radius.analyze_blast_radius"


def _make_runner(run_id: str = "run-1") -> MagicMock:
    """Create a mock WorkflowRunner."""
    runner = MagicMock()
    runner.run_id = run_id
    runner._db_path = None
    runner.resolve_target = AsyncMock(return_value=MagicMock(id="target-1"))
    runner.emit_progress = AsyncMock()
    runner.emit_finding = AsyncMock()
    runner.complete = AsyncMock()
    runner.fail = AsyncMock()
    return runner


def _base_config() -> dict:
    """Create a minimal valid config."""
    return {
        "target_id": "target-1",
        "chain_execution_id": "abcdef1234567890",
    }


def _mock_conn(exec_row: dict | None, step_rows: list[dict] | None = None) -> MagicMock:
    """Build a mock connection context manager.

    Args:
        exec_row: Row to return for the chain_executions query, or None.
        step_rows: Rows to return for the chain_step_outputs query.
    """
    conn = MagicMock()

    # First execute() call -> fetchone() returns exec_row
    # Second execute() call -> fetchall() returns step_rows
    exec_cursor = MagicMock()
    exec_cursor.fetchone.return_value = exec_row

    step_cursor = MagicMock()
    step_cursor.fetchall.return_value = step_rows or []

    conn.execute.side_effect = [exec_cursor, step_cursor]

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestBlastRadiusWorkflow:
    """Tests for the measure_blast_radius workflow executor."""

    async def test_success_with_boundaries(self) -> None:
        """2 trust boundaries -> 3 findings (2 boundary + 1 summary), COMPLETED."""
        runner = _make_runner()
        config = _base_config()

        exec_row = {
            "id": config["chain_execution_id"],
            "trust_boundaries": json.dumps(["network-to-internal", "user-to-admin"]),
        }
        step_rows = [
            {"success": 1, "step_id": "s1", "module": "inject"},
            {"success": 1, "step_id": "s2", "module": "inject"},
        ]

        mock_analysis = {
            "systems_touched": ["system-a", "system-b"],
            "data_reached": [],
        }

        mock_ctx = _mock_conn(exec_row, step_rows)

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_ANALYZE_PATCH, return_value=mock_analysis),
        ):
            await measure_blast_radius(runner, config)

        # 2 boundary findings + 1 summary = 3
        assert runner.emit_finding.await_count == 3
        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_execution_not_found(self) -> None:
        """fetchone returns None -> FAILED, no findings."""
        runner = _make_runner()
        config = _base_config()

        mock_ctx = _mock_conn(None)

        with patch(_GET_CONN_PATCH, return_value=mock_ctx):
            await measure_blast_radius(runner, config)

        runner.emit_finding.assert_not_awaited()
        runner.complete.assert_awaited_once_with(RunStatus.FAILED)

    async def test_no_trust_boundaries(self) -> None:
        """Empty boundaries list -> 1 summary finding, COMPLETED."""
        runner = _make_runner()
        config = _base_config()

        exec_row = {
            "id": config["chain_execution_id"],
            "trust_boundaries": json.dumps([]),
        }
        step_rows = [
            {"success": 1, "step_id": "s1", "module": "inject"},
        ]

        mock_analysis = {
            "systems_touched": ["system-a"],
            "data_reached": [],
        }

        mock_ctx = _mock_conn(exec_row, step_rows)

        with (
            patch(_GET_CONN_PATCH, return_value=mock_ctx),
            patch(_ANALYZE_PATCH, return_value=mock_analysis),
        ):
            await measure_blast_radius(runner, config)

        # 0 boundary findings + 1 summary = 1
        assert runner.emit_finding.await_count == 1
        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_db_error(self) -> None:
        """get_connection raises -> FAILED."""
        runner = _make_runner()
        config = _base_config()

        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(side_effect=RuntimeError("db error"))
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch(_GET_CONN_PATCH, return_value=mock_ctx):
            await measure_blast_radius(runner, config)

        runner.emit_finding.assert_not_awaited()
        runner.complete.assert_awaited_once_with(RunStatus.FAILED)
