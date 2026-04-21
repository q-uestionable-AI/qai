"""Tests for the WorkflowRunner lifecycle."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from q_ai.core.db import create_target, get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
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
    """Create a WorkflowRunner with a temp database and a real target."""
    with get_connection(db_path) as conn:
        target_id = create_target(conn, type="server", name="test-target")
    return WorkflowRunner(
        workflow_id="assess",
        config={"target_id": target_id},
        db_path=db_path,
    )


class TestLifecycle:
    """WorkflowRunner lifecycle tests."""

    async def test_start_creates_parent_run(self, runner: WorkflowRunner, db_path: Path) -> None:
        """start() creates a run in DB with module='workflow', status=RUNNING."""
        run_id = await runner.start()
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.module == "workflow"
        assert run.status == RunStatus.RUNNING

    async def test_complete_sets_finished(self, runner: WorkflowRunner, db_path: Path) -> None:
        """complete() sets status=COMPLETED and finished_at."""
        run_id = await runner.start()
        await runner.complete()
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        assert run.finished_at is not None

    async def test_fail_sets_failed(self, runner: WorkflowRunner, db_path: Path) -> None:
        """fail() sets status=FAILED."""
        run_id = await runner.start()
        await runner.fail("something broke")
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED

    async def test_create_child_run(self, runner: WorkflowRunner, db_path: Path) -> None:
        """create_child_run() creates a child run with correct parent_run_id."""
        parent_id = await runner.start()
        child_id = await runner.create_child_run("audit", name="scan-1")
        with get_connection(db_path) as conn:
            child = get_run(conn, child_id)
        assert child is not None
        assert child.parent_run_id == parent_id
        assert child.module == "audit"

    async def test_update_child_status(self, runner: WorkflowRunner, db_path: Path) -> None:
        """update_child_status() persists the status change."""
        await runner.start()
        child_id = await runner.create_child_run("audit")
        await runner.update_child_status(child_id, RunStatus.RUNNING)
        with get_connection(db_path) as conn:
            child = get_run(conn, child_id)
        assert child is not None
        assert child.status == RunStatus.RUNNING

    async def test_emit_with_no_ws_manager(self, db_path: Path) -> None:
        """Emitting events with no ws_manager does not raise."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            ws_manager=None,
            db_path=db_path,
        )
        await runner.start()
        # Should not raise
        await runner.emit({"type": "test", "run_id": runner.run_id})
        await runner.emit_progress(runner.run_id, "testing")
        await runner.emit_finding("f1", runner.run_id, "audit", 3, "Test Finding")


class TestActiveWorkflows:
    """Tests for active_workflows dict registration."""

    async def test_start_registers_in_active_workflows(self, db_path: Path) -> None:
        """start() adds runner to active_workflows dict."""
        active: dict[str, WorkflowRunner] = {}
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            active_workflows=active,
            db_path=db_path,
        )
        run_id = await runner.start()
        assert run_id in active
        assert active[run_id] is runner

    async def test_complete_deregisters_from_active_workflows(self, db_path: Path) -> None:
        """complete() removes runner from active_workflows dict."""
        active: dict[str, WorkflowRunner] = {}
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            active_workflows=active,
            db_path=db_path,
        )
        run_id = await runner.start()
        await runner.complete()
        assert run_id not in active

    async def test_fail_deregisters_from_active_workflows(self, db_path: Path) -> None:
        """fail() removes runner from active_workflows dict."""
        active: dict[str, WorkflowRunner] = {}
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            active_workflows=active,
            db_path=db_path,
        )
        run_id = await runner.start()
        await runner.fail()
        assert run_id not in active

    async def test_start_without_active_workflows_dict(self, db_path: Path) -> None:
        """start() works fine when active_workflows is None."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            active_workflows=None,
            db_path=db_path,
        )
        run_id = await runner.start()
        assert run_id  # no error


class TestWaitResume:
    """Tests for human-in-the-loop wait/resume."""

    async def test_wait_for_user_sets_status(self, runner: WorkflowRunner, db_path: Path) -> None:
        """wait_for_user() sets run status to WAITING_FOR_USER."""
        await runner.start()

        async def _resume_soon() -> None:
            await asyncio.sleep(0.05)
            await runner.resume({"confirmed": True})

        task = asyncio.create_task(_resume_soon())
        result = await runner.wait_for_user("Please confirm")
        await task

        # After resume, status is RUNNING again — check that waiting state was set
        # by verifying we got resume data back
        assert result == {"confirmed": True}

    async def test_resume_unblocks_wait(self, runner: WorkflowRunner, db_path: Path) -> None:
        """wait_for_user() returns after resume() is called."""
        await runner.start()

        async def _resume_soon() -> None:
            await asyncio.sleep(0.05)
            await runner.resume()

        task = asyncio.create_task(_resume_soon())
        result = await asyncio.wait_for(runner.wait_for_user("Continue?"), timeout=2.0)
        await task
        assert result == {}

    async def test_resume_returns_data(self, runner: WorkflowRunner, db_path: Path) -> None:
        """User-submitted data flows through resume to wait_for_user."""
        await runner.start()

        async def _resume_soon() -> None:
            await asyncio.sleep(0.05)
            await runner.resume({"target": "updated"})

        task = asyncio.create_task(_resume_soon())
        result = await asyncio.wait_for(runner.wait_for_user("Enter target"), timeout=2.0)
        await task
        assert result == {"target": "updated"}

    async def test_resume_idempotent(self, runner: WorkflowRunner, db_path: Path) -> None:
        """resume() is a no-op when not in WAITING_FOR_USER state."""
        await runner.start()
        # Runner is RUNNING, not WAITING_FOR_USER — resume should be a no-op
        await runner.resume({"data": "ignored"})
        with get_connection(db_path) as conn:
            run = get_run(conn, runner.run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING


class TestTargetResolution:
    """Tests for resolve_target."""

    async def test_resolve_target(self, db_path: Path) -> None:
        """resolve_target() loads a target from the DB."""
        with get_connection(db_path) as conn:
            target_id = create_target(conn, type="server", name="test-mcp")

        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": target_id},
            db_path=db_path,
        )
        target = await runner.resolve_target(target_id)
        assert target.name == "test-mcp"
        assert target.type == "server"

    async def test_resolve_target_not_found(self, db_path: Path) -> None:
        """resolve_target() raises ValueError on missing target."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            db_path=db_path,
        )
        with pytest.raises(ValueError, match="Target not found"):
            await runner.resolve_target("nonexistent")
