"""Tests for the IPI adapter."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.ipi.adapter import IPIAdapter, IPIAdapterResult, RetrievalGate
from q_ai.ipi.generate_service import GenerateResult
from q_ai.ipi.models import Campaign
from q_ai.orchestrator.runner import WorkflowRunner


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a temporary database with schema applied."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def runner(db_path: Path) -> WorkflowRunner:
    """Create a WorkflowRunner with a temp database."""
    return WorkflowRunner(
        workflow_id="test-workflow",
        config={},
        db_path=db_path,
    )


def _make_generate_result(count: int = 2) -> GenerateResult:
    """Build a GenerateResult for testing."""
    campaigns = [
        Campaign(
            id=f"camp-{i}",
            uuid=f"uuid-{i}",
            token=f"token-{i}",
            filename=f"report_{i}.pdf",
            format="pdf",
            technique="white_ink",
            callback_url=f"http://localhost:8080/c/uuid-{i}",
        )
        for i in range(count)
    ]
    return GenerateResult(campaigns=campaigns)


_BASE_CONFIG: dict = {
    "callback_url": "http://localhost:8080",
    "output_dir": "/tmp/ipi-output",
    "format": "pdf",
    "techniques": ["white_ink"],
}


class TestIPIAdapter:
    """Tests for IPIAdapter orchestration glue."""

    async def test_ipi_run_generates_payloads(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Mock generate_documents -> WAITING_FOR_USER called."""
        await runner.start()
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock) as mock_wait,
        ):
            await adapter.run()

        mock_wait.assert_called_once()
        assert "Deploy" in mock_wait.call_args.args[0]

    async def test_ipi_run_resumes_to_completed(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Mock wait_for_user returns -> COMPLETED."""
        await runner.start()
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        assert isinstance(result, IPIAdapterResult)
        assert result.payload_count == 2
        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_ipi_persist_called_with_child_id(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Verify persist_generate called with run_id=child_id."""
        await runner.start()
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate") as mock_persist,
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        mock_persist.assert_called_once()
        assert mock_persist.call_args.kwargs["run_id"] == result.run_id

    async def test_ipi_invalid_format_fails(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Bad format string -> FAILED, raises ValueError."""
        await runner.start()
        config = {**_BASE_CONFIG, "format": "not_a_format"}
        adapter = IPIAdapter(runner, config)

        with pytest.raises(ValueError, match="Invalid IPI format"):
            await adapter.run()

        with get_connection(db_path) as conn:
            children = conn.execute(
                "SELECT id FROM runs WHERE parent_run_id = ?", (runner.run_id,)
            ).fetchall()
            assert len(children) == 1
            child = get_run(conn, children[0]["id"])
        assert child is not None
        assert child.status == RunStatus.FAILED

    async def test_gate_non_viable_skips_generation(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Non-viable gate (retrieval_rate=0) skips generation entirely."""
        await runner.start()
        gate = RetrievalGate(
            retrieval_rate=0.0,
            query_viability={"q1": False, "q2": False},
        )
        config = {**_BASE_CONFIG, "retrieval_gate": gate}
        adapter = IPIAdapter(runner, config)

        with patch("q_ai.ipi.adapter.generate_documents") as mock_gen:
            result = await adapter.run()

        mock_gen.assert_not_called()
        assert result.payload_count == 0
        assert result.generate_result is None
        assert result.gated is True
        assert set(result.non_viable_queries) == {"q1", "q2"}

        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_gate_viable_generates_normally(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Viable gate (retrieval_rate > 0) generates payloads normally."""
        await runner.start()
        gate = RetrievalGate(
            retrieval_rate=0.5,
            query_viability={"q1": True, "q2": False},
        )
        config = {**_BASE_CONFIG, "retrieval_gate": gate}
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, config)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        assert result.payload_count == 2
        assert result.gated is True
        assert result.non_viable_queries == ["q2"]
        assert result.generate_result is gen_result

    async def test_no_gate_runs_ungated(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Without retrieval_gate in config, runs ungated."""
        await runner.start()
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        assert result.gated is False
        assert result.non_viable_queries == []

    async def test_gate_non_viable_persists_retrieval_gate_evidence(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Non-viable gate stores retrieval_gate evidence on the IPI child run."""
        await runner.start()
        gate = RetrievalGate(
            retrieval_rate=0.0,
            query_viability={"q1": False, "q2": False},
        )
        config = {**_BASE_CONFIG, "retrieval_gate": gate}
        adapter = IPIAdapter(runner, config)

        with patch("q_ai.ipi.adapter.generate_documents"):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            ev_row = conn.execute(
                "SELECT content FROM evidence WHERE run_id = ? AND type = 'retrieval_gate'",
                (result.run_id,),
            ).fetchone()
        assert ev_row is not None
        data = json.loads(ev_row["content"])
        assert data["gated"] is True
        assert set(data["non_viable_queries"]) == {"q1", "q2"}
        assert data["retrieval_rate"] == 0.0
        assert data["threshold"] == 0.0

    async def test_gate_viable_persists_retrieval_gate_evidence(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Viable gate also stores retrieval_gate evidence with partial viability."""
        await runner.start()
        gate = RetrievalGate(
            retrieval_rate=0.5,
            query_viability={"q1": True, "q2": False},
        )
        config = {**_BASE_CONFIG, "retrieval_gate": gate}
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, config)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            ev_row = conn.execute(
                "SELECT content FROM evidence WHERE run_id = ? AND type = 'retrieval_gate'",
                (result.run_id,),
            ).fetchone()
        assert ev_row is not None
        data = json.loads(ev_row["content"])
        assert data["gated"] is True
        assert data["retrieval_rate"] == 0.5
        assert data["non_viable_queries"] == ["q2"]

    async def test_no_gate_no_retrieval_gate_evidence(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Without retrieval_gate, no retrieval_gate evidence is stored."""
        await runner.start()
        gen_result = _make_generate_result()

        adapter = IPIAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.ipi.adapter.generate_documents", return_value=gen_result),
            patch("q_ai.ipi.adapter.persist_generate"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            ev_row = conn.execute(
                "SELECT content FROM evidence WHERE run_id = ? AND type = 'retrieval_gate'",
                (result.run_id,),
            ).fetchone()
        assert ev_row is None
