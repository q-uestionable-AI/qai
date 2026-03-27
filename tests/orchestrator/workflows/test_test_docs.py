"""Tests for the test_document_ingestion workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import RunStatus
from q_ai.ipi.adapter import RetrievalGate
from q_ai.orchestrator.workflows.test_docs import (
    _build_retrieval_gate,
)
from q_ai.orchestrator.workflows.test_docs import (
    test_document_ingestion as _test_document_ingestion,
)
from q_ai.rxp.adapter import RXPAdapterResult
from q_ai.rxp.models import QueryResult, RetrievalHit, ValidationResult

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

    async def test_rxp_success_passes_gate_to_ipi(self, tmp_path: Path) -> None:
        """When RXP succeeds, IPI receives a RetrievalGate in config."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        config["rxp_enabled"] = True
        config["rxp"] = {"model_id": "all-MiniLM-L6-v2", "profile_id": None}

        rxp_result = _make_rxp_result(retrieval_rate=0.5, queries_viable={"q1": True, "q2": False})

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(_RXP_PATCH) as MockRXP,
        ):
            MockRXP.return_value.run = AsyncMock(return_value=rxp_result)
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        # Verify IPI was constructed with a retrieval_gate in config
        ipi_config = MockIPI.call_args[0][1]
        assert "retrieval_gate" in ipi_config
        gate = ipi_config["retrieval_gate"]
        assert isinstance(gate, RetrievalGate)
        assert gate.retrieval_rate == 0.5
        assert gate.query_viability == {"q1": True, "q2": False}

    async def test_rxp_failure_ipi_runs_ungated(self, tmp_path: Path) -> None:
        """When RXP fails, IPI config has no retrieval_gate."""
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

        ipi_config = MockIPI.call_args[0][1]
        assert "retrieval_gate" not in ipi_config

    async def test_rxp_disabled_ipi_runs_ungated(self, tmp_path: Path) -> None:
        """When RXP is disabled, IPI config has no retrieval_gate."""
        runner = _make_runner()
        config = _base_config(tmp_path)
        assert config["rxp_enabled"] is False

        with patch(_IPI_PATCH) as MockIPI:
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        ipi_config = MockIPI.call_args[0][1]
        assert "retrieval_gate" not in ipi_config


class TestBuildRetrievalGate:
    """Tests for _build_retrieval_gate helper."""

    def test_builds_gate_from_rxp_result(self) -> None:
        """Gate reflects per-query viability from RXP."""
        rxp_result = _make_rxp_result(
            retrieval_rate=0.5,
            queries_viable={"q1": True, "q2": False},
        )
        gate = _build_retrieval_gate(rxp_result, {})
        assert gate.retrieval_rate == 0.5
        assert gate.query_viability == {"q1": True, "q2": False}
        assert gate.threshold == 0.0

    def test_custom_threshold(self) -> None:
        """Threshold is read from config."""
        rxp_result = _make_rxp_result(retrieval_rate=0.5, queries_viable={"q1": True})
        gate = _build_retrieval_gate(rxp_result, {"rxp_retrieval_threshold": 0.75})
        assert gate.threshold == 0.75

    def test_zero_retrieval_rate(self) -> None:
        """All queries non-viable produces non-viable gate."""
        rxp_result = _make_rxp_result(
            retrieval_rate=0.0,
            queries_viable={"q1": False, "q2": False},
        )
        gate = _build_retrieval_gate(rxp_result, {})
        assert not gate.viable
        assert gate.non_viable_queries == ["q1", "q2"]


def _make_rxp_result(
    retrieval_rate: float,
    queries_viable: dict[str, bool],
) -> RXPAdapterResult:
    """Build a mock RXPAdapterResult with per-query data."""
    query_results = [
        QueryResult(
            query=q,
            model_id="test-model",
            top_k=5,
            hits=[
                RetrievalHit(
                    document_id="poison-1",
                    rank=1,
                    distance=0.1,
                    is_poison=True,
                )
            ]
            if viable
            else [],
            poison_retrieved=viable,
            poison_rank=1 if viable else None,
        )
        for q, viable in queries_viable.items()
    ]
    validation = ValidationResult(
        model_id="test-model",
        total_queries=len(queries_viable),
        poison_retrievals=sum(1 for v in queries_viable.values() if v),
        retrieval_rate=retrieval_rate,
        mean_poison_rank=1.0 if retrieval_rate > 0 else None,
        query_results=query_results,
    )
    return RXPAdapterResult(
        run_id="rxp-child-1",
        result=validation,
        retrieval_rate=retrieval_rate,
    )
