"""Tests for the workflow launch API."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _valid_body() -> dict:
    """Return a valid launch request body."""
    return {
        "target_name": "test-server",
        "transport": "stdio",
        "command": "echo hi",
        "model": "openai/gpt-4",
        "rounds": 1,
    }


def _noop_executor() -> AsyncMock:
    """Return an async no-op to replace the workflow executor."""
    return AsyncMock()


class TestLaunchCreatesTarget:
    """POST /api/workflows/launch with valid config creates a target."""

    def test_launch_creates_target(self, client: TestClient, tmp_db: Path) -> None:
        """POST valid config -> target created in DB."""
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch(
                "q_ai.server.routes.get_workflow",
            ) as mock_get_wf,
        ):
            executor = _noop_executor()
            mock_get_wf.return_value.executor = executor
            mock_get_wf.return_value.id = "assess"

            resp = client.post("/api/workflows/launch", json=_valid_body())

        assert resp.status_code == 201

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM targets WHERE name = ?", ("test-server",)).fetchall()
            assert len(rows) == 1
            assert rows[0]["type"] == "server"
        finally:
            conn.close()


class TestLaunchCreatesWorkflowRun:
    """POST /api/workflows/launch creates a workflow run in DB."""

    def test_launch_creates_workflow_run(self, client: TestClient, tmp_db: Path) -> None:
        """POST valid config -> run with module='workflow' exists in DB."""
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_get_wf,
        ):
            executor = _noop_executor()
            mock_get_wf.return_value.executor = executor
            mock_get_wf.return_value.id = "assess"

            resp = client.post("/api/workflows/launch", json=_valid_body())

        assert resp.status_code == 201

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM runs WHERE module = ?", ("workflow",)).fetchall()
            assert len(rows) >= 1
        finally:
            conn.close()


class TestLaunchReturnsRunId:
    """POST /api/workflows/launch returns run_id and redirect."""

    def test_launch_returns_run_id(self, client: TestClient) -> None:
        """Response JSON contains run_id and redirect keys."""
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_get_wf,
        ):
            executor = _noop_executor()
            mock_get_wf.return_value.executor = executor
            mock_get_wf.return_value.id = "assess"

            resp = client.post("/api/workflows/launch", json=_valid_body())

        assert resp.status_code == 201
        data = resp.json()
        assert "run_id" in data
        assert "redirect" in data
        assert data["run_id"] in data["redirect"]


class TestLaunchValidation:
    """Validation tests for the launch endpoint."""

    def test_launch_validation_missing_transport(self, client: TestClient) -> None:
        """POST without transport -> 422."""
        body = _valid_body()
        body["transport"] = ""
        resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422

    def test_launch_validation_missing_model(self, client: TestClient) -> None:
        """POST without model -> 422."""
        body = _valid_body()
        body["model"] = ""
        resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422

    def test_launch_validation_missing_credential(self, client: TestClient) -> None:
        """POST with valid model but no credential -> 422."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.post("/api/workflows/launch", json=_valid_body())
        assert resp.status_code == 422

    def test_launch_validation_missing_target_name(self, client: TestClient) -> None:
        """POST without target_name -> 422."""
        body = _valid_body()
        body["target_name"] = ""
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
