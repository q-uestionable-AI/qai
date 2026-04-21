"""Tests for WebSocket route and workflow resume endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


class TestWSEndpoint:
    """WebSocket /ws endpoint tests."""

    def test_ws_endpoint_accepts_connection(self, client: TestClient) -> None:
        """The /ws endpoint accepts WebSocket connections."""
        with client.websocket_connect("/ws", headers={"origin": "http://localhost:8000"}) as ws:
            # Connection established — send a message to verify two-way
            ws.send_text("ping")


class TestResumeEndpoint:
    """POST /api/workflows/{run_id}/resume endpoint tests."""

    def test_resume_endpoint_404_no_active_workflow(self, client: TestClient) -> None:
        """Returns 404 when no active workflow exists for the run_id."""
        resp = client.post("/api/workflows/nonexistent/resume")
        assert resp.status_code == 404
        assert "No active workflow" in resp.json()["detail"]

    def test_resume_endpoint_409_not_waiting(self, client: TestClient, tmp_db: Path) -> None:
        """Returns 409 when the run exists but is not in WAITING_FOR_USER."""
        from q_ai.core.db import create_run, get_connection, update_run_status
        from q_ai.core.models import RunStatus
        from q_ai.orchestrator.runner import WorkflowRunner

        # Create a run directly in the DB (status RUNNING)
        with get_connection(tmp_db) as conn:
            run_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, run_id, RunStatus.RUNNING)

        # Create a runner and manually register it in active_workflows
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            db_path=tmp_db,
        )
        runner._run_id = run_id
        client.app.state.active_workflows[run_id] = runner

        resp = client.post(f"/api/workflows/{run_id}/resume")
        assert resp.status_code == 409
        assert "not waiting" in resp.json()["detail"]

        # Cleanup
        client.app.state.active_workflows.pop(run_id, None)

    def test_resume_endpoint_400_malformed_json(self, client: TestClient, tmp_db: Path) -> None:
        """Returns 400 when request body is malformed JSON."""
        from q_ai.core.db import create_run, get_connection, update_run_status
        from q_ai.core.models import RunStatus
        from q_ai.orchestrator.runner import WorkflowRunner

        with get_connection(tmp_db) as conn:
            run_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, run_id, RunStatus.WAITING_FOR_USER)

        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            db_path=tmp_db,
        )
        runner._run_id = run_id
        client.app.state.active_workflows[run_id] = runner

        resp = client.post(
            f"/api/workflows/{run_id}/resume",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

        # Cleanup
        client.app.state.active_workflows.pop(run_id, None)
