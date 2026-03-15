"""Tests for the proxy adapter."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.orchestrator.runner import WorkflowRunner
from q_ai.proxy.adapter import ProxyAdapter, ProxyResult


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
        workflow_id="assess",
        config={},
        db_path=db_path,
    )


def _mock_adapter() -> AsyncMock:
    """Create a mock TransportAdapter."""
    adapter = AsyncMock()
    adapter.__aenter__ = AsyncMock(return_value=adapter)
    adapter.__aexit__ = AsyncMock(return_value=False)
    return adapter


class TestProxyAdapter:
    """Tests for ProxyAdapter lifecycle management."""

    async def test_start_creates_child_run(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify child run in DB with module='proxy', status=RUNNING."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        adapter = ProxyAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", new_callable=AsyncMock),
        ):
            child_id = await adapter.start()

        with get_connection(db_path) as conn:
            child = get_run(conn, child_id)
        assert child is not None
        assert child.module == "proxy"
        assert child.parent_run_id == runner.run_id
        assert child.status == RunStatus.RUNNING

        # Clean up
        await adapter.stop()

    async def test_start_launches_background_task(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Verify self._task is set and not done."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        # Make run_pipeline block until cancelled
        pipeline_event = asyncio.Event()

        async def _blocking_pipeline(*args: object, **kwargs: object) -> None:
            await pipeline_event.wait()

        adapter = ProxyAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", side_effect=_blocking_pipeline),
        ):
            await adapter.start()

        assert adapter._task is not None
        assert not adapter._task.done()

        # Clean up
        pipeline_event.set()
        await adapter.stop()

    async def test_stop_cancels_task(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify task is cancelled after stop()."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        pipeline_event = asyncio.Event()

        async def _blocking_pipeline(*args: object, **kwargs: object) -> None:
            await pipeline_event.wait()

        adapter = ProxyAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", side_effect=_blocking_pipeline),
            patch("q_ai.proxy.adapter.persist_session"),
        ):
            await adapter.start()
            await adapter.stop()

        assert adapter._task is not None
        assert adapter._task.done()

    async def test_stop_persists_session(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify proxy_sessions row created with correct run_id."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        adapter = ProxyAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", new_callable=AsyncMock),
        ):
            child_id = await adapter.start()
            # Let the pipeline "finish" quickly
            await asyncio.sleep(0.01)

        with patch("q_ai.proxy.adapter.persist_session") as mock_persist:
            await adapter.stop()
            mock_persist.assert_called_once()
            call_kwargs = mock_persist.call_args
            assert call_kwargs.kwargs.get("run_id") == child_id

    async def test_stop_sets_completed(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify child run status=COMPLETED after stop()."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        adapter = ProxyAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", new_callable=AsyncMock),
            patch("q_ai.proxy.adapter.persist_session"),
        ):
            child_id = await adapter.start()
            await asyncio.sleep(0.01)
            await adapter.stop()

        with get_connection(db_path) as conn:
            child = get_run(conn, child_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_stop_returns_proxy_result(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify ProxyResult fields."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        adapter = ProxyAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", new_callable=AsyncMock),
            patch("q_ai.proxy.adapter.persist_session"),
        ):
            await adapter.start()
            await asyncio.sleep(0.01)
            result = await adapter.stop()

        assert isinstance(result, ProxyResult)
        assert result.run_id
        assert result.message_count == 0  # No actual messages in mock
        assert result.duration_seconds >= 0

    async def test_start_stop_lifecycle(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Full start -> stop cycle verifies clean state."""
        await runner.start()

        client_adapter = _mock_adapter()
        server_adapter = _mock_adapter()

        pipeline_event = asyncio.Event()

        async def _blocking_pipeline(*args: object, **kwargs: object) -> None:
            await pipeline_event.wait()

        adapter = ProxyAdapter(runner, {"transport": "sse", "url": "http://localhost:3000/sse"})

        with (
            patch(
                "q_ai.proxy.adapter._create_adapters",
                return_value=(client_adapter, server_adapter),
            ),
            patch("q_ai.proxy.adapter.run_pipeline", side_effect=_blocking_pipeline),
            patch("q_ai.proxy.adapter.persist_session"),
        ):
            child_id = await adapter.start()

            # Task should be running
            assert adapter._task is not None
            assert not adapter._task.done()

            result = await adapter.stop()

        # Task should be done
        assert adapter._task.done()

        # Result should be valid
        assert result.run_id == child_id
        assert result.duration_seconds >= 0

        # Child run should be completed
        with get_connection(db_path) as conn:
            child = get_run(conn, child_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED
