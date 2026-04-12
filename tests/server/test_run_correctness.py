"""Regression tests for run correctness (Phase 0).

Covers four bugs:
1. Duplicate target names must create distinct runs
2. Parent terminal-state propagation via WebSocket event structure
3. Elapsed timer DB timestamps are set correctly
4. Progress messages do not overwrite the workflow name element
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from q_ai.core.db import (
    create_run,
    create_target,
    get_connection,
    get_run,
    update_run_status,
)
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.orchestrator.runner import WorkflowRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Bug #1: Duplicate target name creates distinct runs
# ---------------------------------------------------------------------------


class TestDuplicateTargetName:
    """Reusing a target name must always create a distinct target and run."""

    def test_duplicate_target_name_creates_distinct_targets(self, db_path: Path) -> None:
        """Two create_target calls with the same name produce different IDs."""
        with get_connection(db_path) as conn:
            id_a = create_target(conn, type="server", name="my-server")
            id_b = create_target(conn, type="server", name="my-server")

        assert id_a != id_b

    def test_duplicate_target_name_via_launch(self, client: TestClient, tmp_db: Path) -> None:
        """Launching twice with the same target name creates two distinct runs."""
        body = {
            "target_name": "same-name",
            "transport": "stdio",
            "command": "echo hi",
            "model": "openai/gpt-4",
            "rounds": 1,
        }
        with (
            patch("q_ai.server.routes.workflows.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.workflows.get_workflow") as mock_get_wf,
        ):
            mock_get_wf.return_value.executor = AsyncMock()
            mock_get_wf.return_value.id = "assess"
            mock_get_wf.return_value.requires_provider = True

            resp1 = client.post("/api/workflows/launch", json=body)
            resp2 = client.post("/api/workflows/launch", json=body)

        assert resp1.status_code == 201
        assert resp2.status_code == 201

        run_id_1 = resp1.json()["run_id"]
        run_id_2 = resp2.json()["run_id"]
        assert run_id_1 != run_id_2

        # Verify both runs exist in DB
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            runs = conn.execute("SELECT * FROM runs WHERE module = ?", ("workflow",)).fetchall()
            run_ids = {r["id"] for r in runs}
            assert run_id_1 in run_ids
            assert run_id_2 in run_ids

            targets = conn.execute(
                "SELECT * FROM targets WHERE name = ?", ("same-name",)
            ).fetchall()
            assert len(targets) == 2
            assert targets[0]["id"] != targets[1]["id"]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Bug #2: Parent terminal-state propagation event structure
# ---------------------------------------------------------------------------


class TestTerminalStateEvent:
    """runner.complete() must emit a run_status event with terminal status."""

    async def test_complete_emits_terminal_event(self, db_path: Path) -> None:
        """complete() emits run_status with correct fields for WebSocket consumers."""
        events: list[dict] = []
        ws_manager = AsyncMock()
        ws_manager.broadcast = AsyncMock(side_effect=events.append)

        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            ws_manager=ws_manager,
            db_path=db_path,
        )
        await runner.start()
        await runner.complete(RunStatus.COMPLETED)

        # Find the terminal event (last run_status with terminal status)
        terminal_events = [
            e
            for e in events
            if e.get("type") == "run_status" and e.get("status") == int(RunStatus.COMPLETED)
        ]
        assert len(terminal_events) == 1
        event = terminal_events[0]
        assert event["run_id"] == runner.run_id
        assert event["module"] == "workflow"
        assert event["status"] == int(RunStatus.COMPLETED)

    async def test_complete_emits_after_db_write(self, db_path: Path) -> None:
        """DB status is terminal BEFORE the event is broadcast."""
        db_status_at_broadcast: list[RunStatus | None] = []

        async def capture_broadcast(event: dict) -> None:
            is_terminal = event.get("type") == "run_status" and event.get("status") == int(
                RunStatus.COMPLETED
            )
            if is_terminal:
                with get_connection(db_path) as conn:
                    run = get_run(conn, event["run_id"])
                db_status_at_broadcast.append(run.status if run else None)

        ws_manager = AsyncMock()
        ws_manager.broadcast = AsyncMock(side_effect=capture_broadcast)

        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            ws_manager=ws_manager,
            db_path=db_path,
        )
        await runner.start()
        await runner.complete(RunStatus.COMPLETED)

        assert db_status_at_broadcast == [RunStatus.COMPLETED]

    async def test_partial_status_emits_terminal_event(self, db_path: Path) -> None:
        """complete(PARTIAL) emits a terminal event recognized by the frontend."""
        events: list[dict] = []
        ws_manager = AsyncMock()
        ws_manager.broadcast = AsyncMock(side_effect=events.append)

        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            ws_manager=ws_manager,
            db_path=db_path,
        )
        await runner.start()
        await runner.complete(RunStatus.PARTIAL)

        terminal_events = [
            e
            for e in events
            if e.get("type") == "run_status" and e.get("status") == int(RunStatus.PARTIAL)
        ]
        assert len(terminal_events) == 1


# ---------------------------------------------------------------------------
# Bug #3: Elapsed timer — DB timestamps
# ---------------------------------------------------------------------------


class TestElapsedTimerTimestamps:
    """DB timestamps must be set correctly for timer calculations."""

    async def test_started_at_set_on_create(self, db_path: Path) -> None:
        """Run has started_at set when created."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            db_path=db_path,
        )
        run_id = await runner.start()
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.started_at is not None

    async def test_finished_at_set_on_complete(self, db_path: Path) -> None:
        """Run has finished_at set when completed."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            db_path=db_path,
        )
        run_id = await runner.start()
        await runner.complete()
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.finished_at is not None

    async def test_finished_at_after_started_at(self, db_path: Path) -> None:
        """finished_at >= started_at for a completed run."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            db_path=db_path,
        )
        run_id = await runner.start()
        await runner.complete()
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.finished_at >= run.started_at

    async def test_finished_at_not_set_while_running(self, db_path: Path) -> None:
        """finished_at is None while the run is still RUNNING."""
        runner = WorkflowRunner(
            workflow_id="assess",
            config={"target_id": "t1"},
            db_path=db_path,
        )
        run_id = await runner.start()
        with get_connection(db_path) as conn:
            run = get_run(conn, run_id)
        assert run is not None
        assert run.finished_at is None


# ---------------------------------------------------------------------------
# Bug #4: Progress messages separate from workflow name
# ---------------------------------------------------------------------------


class TestProgressSeparateFromWorkflowName:
    """Status bar must have separate elements for workflow name and progress."""

    def test_status_bar_has_progress_element(self, client: TestClient, tmp_db: Path) -> None:
        """Status bar partial includes workflow-progress element separate from workflow-name."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, run_id, RunStatus.RUNNING)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/operations/workflow-status-bar?run_id={run_id}")
        assert resp.status_code == 200
        assert 'id="workflow-name"' in resp.text
        assert 'id="workflow-progress"' in resp.text

    def test_workflow_name_shows_workflow_identity(self, client: TestClient, tmp_db: Path) -> None:
        """The workflow-name element contains the workflow name, not progress text."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, run_id, RunStatus.RUNNING)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/operations/workflow-status-bar?run_id={run_id}")
        assert resp.status_code == 200
        # The workflow-name span should contain the display name, not the ID
        assert ">Assess an MCP Server</span>" in resp.text

    def test_idle_operations_shows_history(self, client: TestClient) -> None:
        """GET /operations without run_id shows run history (no status bar)."""
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "Run History" in resp.text
