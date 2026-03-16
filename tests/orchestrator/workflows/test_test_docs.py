"""Tests for the test_document_ingestion workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import RunStatus
from q_ai.orchestrator.workflows.test_docs import (
    test_document_ingestion as _test_document_ingestion,
)

_IPI_PATCH = "q_ai.orchestrator.workflows.test_docs.IPIAdapter"
_RXP_PATCH = "q_ai.orchestrator.workflows.test_docs.RXPAdapter"


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
        "callback_url": "http://localhost:8765/callback",
        "output_dir": str(tmp_path / "ipi-out"),
        "format": "pdf",
        "payload_style": "obvious",
        "payload_type": "callback",
        "base_name": "report",
        "rxp_enabled": False,
    }


class TestTestDocsWorkflow:
    """Tests for the test_document_ingestion workflow executor."""

    async def test_ipi_success_rxp_disabled(self, tmp_path: Path) -> None:
        """RXP skipped, IPI succeeds -> COMPLETED."""
        runner = _make_runner()
        config = _base_config(tmp_path)

        with patch(_IPI_PATCH) as MockIPI:
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_rxp_and_ipi_success(self, tmp_path: Path) -> None:
        """Both RXP and IPI run and succeed -> COMPLETED."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["rxp_enabled"] = True
        config["rxp"] = {"model_id": "all-MiniLM-L6-v2", "profile_id": None}

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(_RXP_PATCH) as MockRXP,
        ):
            MockIPI.return_value.run = AsyncMock()
            MockRXP.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        MockRXP.return_value.run.assert_awaited_once()
        MockIPI.return_value.run.assert_awaited_once()
        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_rxp_failure_ipi_still_runs(self, tmp_path: Path) -> None:
        """RXP raises, IPI succeeds -> PARTIAL."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["rxp_enabled"] = True
        config["rxp"] = {"model_id": "all-MiniLM-L6-v2", "profile_id": None}

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(_RXP_PATCH) as MockRXP,
        ):
            MockRXP.return_value.run = AsyncMock(side_effect=RuntimeError("rxp fail"))
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        MockIPI.return_value.run.assert_awaited_once()
        runner.complete.assert_awaited_once_with(RunStatus.PARTIAL)

    async def test_ipi_failure(self, tmp_path: Path) -> None:
        """IPI raises -> PARTIAL."""
        runner = _make_runner()
        config = _base_config(tmp_path)

        with patch(_IPI_PATCH) as MockIPI:
            MockIPI.return_value.run = AsyncMock(side_effect=RuntimeError("ipi fail"))
            await _test_document_ingestion(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.PARTIAL)

    async def test_rxp_not_called_when_disabled(self, tmp_path: Path) -> None:
        """RXPAdapter never instantiated when rxp_enabled=False."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        assert config["rxp_enabled"] is False

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(_RXP_PATCH) as MockRXP,
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        MockRXP.assert_not_called()
