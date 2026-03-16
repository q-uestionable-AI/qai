"""Tests for the workflow launch API."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
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


# ---------------------------------------------------------------------------
# Helper for new workflow mocks
# ---------------------------------------------------------------------------


def _mock_workflow_entry(workflow_id: str, *, requires_provider: bool = True) -> MagicMock:
    """Create a mock WorkflowEntry for testing."""
    entry = MagicMock()
    entry.id = workflow_id
    entry.executor = _noop_executor()
    entry.requires_provider = requires_provider
    return entry


# ---------------------------------------------------------------------------
# Test Document Ingestion
# ---------------------------------------------------------------------------


class TestLaunchTestDocs:
    """POST /api/workflows/launch with workflow_id=test_docs."""

    def test_valid_body_returns_201(self, client: TestClient) -> None:
        """Valid test_docs body -> 201."""
        body = {
            "workflow_id": "test_docs",
            "target_name": "doc-target",
            "callback_url": "https://example.com/callback",
            "format": "pdf",
            "payload_style": "obvious",
            "payload_type": "callback",
        }
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry("test_docs", requires_provider=False)
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 201

    def test_missing_callback_url_returns_422(self, client: TestClient) -> None:
        """Missing callback_url -> 422."""
        body = {
            "workflow_id": "test_docs",
            "target_name": "doc-target",
            "format": "pdf",
        }
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry("test_docs", requires_provider=False)
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "callback_url" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test a Coding Assistant
# ---------------------------------------------------------------------------


class TestLaunchTestAssistant:
    """POST /api/workflows/launch with workflow_id=test_assistant."""

    def test_valid_body_returns_201(self, client: TestClient) -> None:
        """Valid test_assistant body -> 201."""
        body = {
            "workflow_id": "test_assistant",
            "target_name": "assistant-target",
            "format_id": "python",
        }
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry(
                "test_assistant", requires_provider=False
            )
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 201

    def test_missing_format_id_returns_422(self, client: TestClient) -> None:
        """Missing format_id -> 422."""
        body = {
            "workflow_id": "test_assistant",
            "target_name": "assistant-target",
        }
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry(
                "test_assistant", requires_provider=False
            )
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "format_id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Trace an Attack Path
# ---------------------------------------------------------------------------


class TestLaunchTracePath:
    """POST /api/workflows/launch with workflow_id=trace_path."""

    def _mock_chain_discovery(self) -> tuple[MagicMock, MagicMock]:
        """Build mock discover_chains and load_chain that return one template."""
        mock_chain = MagicMock()
        mock_chain.id = "test-chain"
        mock_chain.name = "Test Chain"

        mock_path = MagicMock(spec=Path)
        mock_path.resolve.return_value = Path("/fake/path/chain.yaml")

        mock_discover = MagicMock(return_value=[mock_path])
        mock_load = MagicMock(return_value=mock_chain)
        return mock_discover, mock_load

    def test_valid_body_returns_201(self, client: TestClient) -> None:
        """Valid trace_path body with known template -> 201."""
        mock_discover, mock_load = self._mock_chain_discovery()
        body = {
            "workflow_id": "trace_path",
            "target_name": "trace-target",
            "chain_template_id": "test-chain",
            "transport": "stdio",
            "command": "echo hi",
            "model": "openai/gpt-4",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_get_wf,
            patch("q_ai.chain.loader.discover_chains", mock_discover),
            patch("q_ai.chain.loader.load_chain", mock_load),
        ):
            mock_get_wf.return_value = _mock_workflow_entry("trace_path")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 201

    def test_unknown_template_returns_422(self, client: TestClient) -> None:
        """Unknown chain_template_id -> 422."""
        mock_discover, mock_load = self._mock_chain_discovery()
        body = {
            "workflow_id": "trace_path",
            "target_name": "trace-target",
            "chain_template_id": "nonexistent",
            "transport": "stdio",
            "command": "echo hi",
            "model": "openai/gpt-4",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_get_wf,
            patch("q_ai.chain.loader.discover_chains", mock_discover),
            patch("q_ai.chain.loader.load_chain", mock_load),
        ):
            mock_get_wf.return_value = _mock_workflow_entry("trace_path")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "Chain template not found" in resp.json()["detail"]

    def test_missing_template_returns_422(self, client: TestClient) -> None:
        """Missing chain_template_id -> 422."""
        body = {
            "workflow_id": "trace_path",
            "target_name": "trace-target",
            "transport": "stdio",
            "command": "echo hi",
            "model": "openai/gpt-4",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_get_wf,
        ):
            mock_get_wf.return_value = _mock_workflow_entry("trace_path")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "chain_template_id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Measure Blast Radius
# ---------------------------------------------------------------------------


class TestLaunchBlastRadius:
    """POST /api/workflows/launch with workflow_id=blast_radius."""

    def _seed_chain_execution(self, db_path: Path) -> str:
        """Insert a chain execution with a parent run row, return execution id."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Create a target first
            conn.execute(
                "INSERT INTO targets (id, type, name, created_at) VALUES (?, ?, ?, ?)",
                ("tgt-1", "server", "blast-target", "2026-01-01"),
            )
            # Create a run
            conn.execute(
                "INSERT INTO runs (id, module, status, target_id, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("run-1", "chain", 5, "tgt-1", "2026-01-01"),
            )
            # Create a chain execution
            conn.execute(
                "INSERT INTO chain_executions "
                "(id, run_id, chain_id, chain_name, dry_run, success, "
                "trust_boundaries, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("ce-1", "run-1", "chain-1", "Test Chain", 0, 1, "[]", "2026-01-01"),
            )
            conn.commit()
        finally:
            conn.close()
        return "ce-1"

    def test_valid_body_returns_201(self, client: TestClient, tmp_db: Path) -> None:
        """Valid blast_radius body with existing execution -> 201."""
        exec_id = self._seed_chain_execution(tmp_db)
        body = {
            "workflow_id": "blast_radius",
            "chain_execution_id": exec_id,
        }
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry("blast_radius", requires_provider=False)
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 201

    def test_missing_execution_id_returns_422(self, client: TestClient) -> None:
        """Missing chain_execution_id -> 422."""
        body = {"workflow_id": "blast_radius"}
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry("blast_radius", requires_provider=False)
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "chain_execution_id" in resp.json()["detail"]

    def test_nonexistent_execution_returns_422(self, client: TestClient, tmp_db: Path) -> None:
        """Non-existent chain_execution_id -> 422."""
        body = {
            "workflow_id": "blast_radius",
            "chain_execution_id": "does-not-exist",
        }
        with patch("q_ai.server.routes.get_workflow") as mock_get_wf:
            mock_get_wf.return_value = _mock_workflow_entry("blast_radius", requires_provider=False)
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "Chain execution not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Workflow ID routing
# ---------------------------------------------------------------------------


class TestWorkflowIdRouting:
    """Routing by workflow_id."""

    def test_unknown_workflow_returns_422(self, client: TestClient) -> None:
        """Unknown workflow_id -> 422."""
        body = {"workflow_id": "nonexistent", "target_name": "x"}
        resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "Unknown workflow" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Provider gate
# ---------------------------------------------------------------------------


class TestProviderGate:
    """Provider credential gating by workflow type."""

    def test_test_assistant_launches_without_provider(self, client: TestClient) -> None:
        """test_assistant does not call get_credential at all."""
        body = {
            "workflow_id": "test_assistant",
            "target_name": "assistant-target",
            "format_id": "python",
        }
        with (
            patch("q_ai.server.routes.get_credential") as mock_cred,
            patch("q_ai.server.routes.get_workflow") as mock_get_wf,
        ):
            mock_get_wf.return_value = _mock_workflow_entry(
                "test_assistant", requires_provider=False
            )
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 201
        mock_cred.assert_not_called()
