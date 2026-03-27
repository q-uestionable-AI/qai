"""Tests for run history routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import (
    create_run,
    create_target,
    get_connection,
    update_run_status,
)
from q_ai.core.models import RunStatus


class TestRunHistoryPage:
    """Tests for the /runs page rendering run history vs single-run results."""

    def test_runs_page_renders_history_when_no_run_id(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert "Run History" in resp.text

    def test_runs_page_renders_results_when_run_id(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            run_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, run_id, RunStatus.COMPLETED)
        resp = client.get(f"/runs?run_id={run_id}")
        assert resp.status_code == 200
        assert "overview-header" in resp.text

    def test_runs_page_shows_empty_state(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert "No runs yet" in resp.text
        assert "Launch a workflow" in resp.text


class TestRunHistoryAPI:
    def test_history_returns_parent_runs_only(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
            create_run(conn, module="audit", parent_run_id=parent_id)
        resp = client.get("/api/runs/history")
        assert resp.status_code == 200
        # Parent run's display name should appear; child should not
        assert "Assess" in resp.text

    def test_history_filter_by_workflow(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            create_run(conn, module="workflow", name="assess")
            create_run(conn, module="workflow", name="test_docs")
        resp = client.get("/api/runs/history?workflow=assess")
        assert resp.status_code == 200
        assert "Assess" in resp.text

    def test_history_filter_by_status(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            r1 = create_run(conn, module="workflow", name="assess")
            r2 = create_run(conn, module="workflow", name="test_docs")
            update_run_status(conn, r1, RunStatus.COMPLETED)
            update_run_status(conn, r2, RunStatus.FAILED)
        resp = client.get("/api/runs/history?status=COMPLETED")
        assert resp.status_code == 200
        assert "Completed" in resp.text


class TestImportRunVisibility:
    """Tests for import run visibility in run history."""

    def test_import_runs_appear_in_history(self, client: TestClient, tmp_db: Path) -> None:
        """Import runs should appear in run history alongside workflow runs."""
        with get_connection(tmp_db) as conn:
            create_run(conn, module="workflow", name="assess")
            import_run_id = create_run(conn, module="import", name="garak-import", source="garak")
            update_run_status(conn, import_run_id, RunStatus.COMPLETED)
        resp = client.get("/api/runs/history")
        assert resp.status_code == 200
        assert "Import (Garak)" in resp.text

    def test_import_run_display_name_from_source(self, client: TestClient, tmp_db: Path) -> None:
        """Import run display name is built from source field."""
        with get_connection(tmp_db) as conn:
            import_run_id = create_run(conn, module="import", name="pyrit-import", source="pyrit")
            update_run_status(conn, import_run_id, RunStatus.COMPLETED)
        resp = client.get("/api/runs/history")
        assert resp.status_code == 200
        assert "Import (Pyrit)" in resp.text

    def test_import_run_shows_source_badge(self, client: TestClient, tmp_db: Path) -> None:
        """Import runs show a source badge with the tool name."""
        with get_connection(tmp_db) as conn:
            import_run_id = create_run(conn, module="import", name="sarif-import", source="sarif")
            update_run_status(conn, import_run_id, RunStatus.COMPLETED)
        resp = client.get("/api/runs/history")
        assert resp.status_code == 200
        assert "badge-ghost" in resp.text
        assert "sarif" in resp.text

    def test_import_runs_excluded_by_workflow_filter(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        """When a workflow filter is applied, import runs should not appear."""
        with get_connection(tmp_db) as conn:
            create_run(conn, module="workflow", name="assess")
            import_run_id = create_run(conn, module="import", name="garak-import", source="garak")
            update_run_status(conn, import_run_id, RunStatus.COMPLETED)
        resp = client.get("/api/runs/history?workflow=assess")
        assert resp.status_code == 200
        assert "Import (Garak)" not in resp.text


class TestExportRunAPI:
    def test_export_returns_json_bundle(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            target_id = create_target(conn, type="server", name="test-srv")
            parent_id = create_run(
                conn,
                module="workflow",
                name="assess",
                target_id=target_id,
                config={"target_id": target_id},
            )
            update_run_status(conn, parent_id, RunStatus.COMPLETED)
        resp = client.get(f"/api/runs/{parent_id}/export")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")
        data = resp.json()
        assert data["schema_version"] == "run-bundle-v1"
        assert data["run"]["id"] == parent_id

    def test_export_nonexistent_run_404(self, client: TestClient) -> None:
        resp = client.get("/api/runs/nonexistent/export")
        assert resp.status_code == 404


class TestDeleteRunAPI:
    def test_delete_run_succeeds(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            parent_id = create_run(conn, module="workflow", name="assess")
        resp = client.delete(f"/api/runs/{parent_id}")
        assert resp.status_code == 200
        # Verify run is gone
        with get_connection(tmp_db) as conn:
            row = conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (parent_id,)).fetchone()
            assert row[0] == 0

    def test_delete_nonexistent_run_404(self, client: TestClient) -> None:
        resp = client.delete("/api/runs/nonexistent")
        assert resp.status_code == 404
