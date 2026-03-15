"""Tests for the IPI adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.ipi.adapter import IPIAdapter, IPIAdapterResult
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
