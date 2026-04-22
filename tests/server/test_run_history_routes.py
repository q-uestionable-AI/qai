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
from q_ai.services import run_service


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
        assert "badge badge-xs badge-ghost" in resp.text
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


class TestIntelRunRedirect:
    """GET /runs redirects target-bound probe/sweep runs into Intel.

    Covers URL-encoding of both the target_id and run_id components of
    the 302 Location header, and confirms non-IPI run types (import,
    workflow) continue to render through the existing view.
    """

    def test_sweep_redirect_encodes_components(self, client: TestClient, tmp_db: Path) -> None:
        # Use target_id and run_id with a space character to exercise
        # urllib.parse.quote(). Direct INSERTs because create_target and
        # create_run normalise to hex UUIDs.
        target_id = "tgt with space"
        run_id = "run id with space"
        with get_connection(tmp_db) as conn:
            conn.execute(
                "INSERT INTO targets (id, type, name, uri, metadata, created_at) "
                "VALUES (?, 'server', 'enc-target', NULL, NULL, '2026-01-01T00:00:00+00:00')",
                (target_id,),
            )
            create_run(
                conn,
                module="ipi-sweep",
                target_id=target_id,
                run_id=run_id,
            )
            update_run_status(conn, run_id, RunStatus.COMPLETED)
        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == (
            "/intel/targets/tgt%20with%20space#sweep-run-run%20id%20with%20space"
        )
        assert resp.headers["cache-control"] == "no-store"

    def test_probe_redirect_encodes_components(self, client: TestClient, tmp_db: Path) -> None:
        target_id = "tgt with space"
        run_id = "probe id with space"
        with get_connection(tmp_db) as conn:
            conn.execute(
                "INSERT INTO targets (id, type, name, uri, metadata, created_at) "
                "VALUES (?, 'server', 'enc-target', NULL, NULL, '2026-01-01T00:00:00+00:00')",
                (target_id,),
            )
            create_run(
                conn,
                module="ipi-probe",
                target_id=target_id,
                run_id=run_id,
            )
            update_run_status(conn, run_id, RunStatus.COMPLETED)
        resp = client.get(f"/runs?run_id={run_id}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == (
            "/intel/targets/tgt%20with%20space#probe-run-probe%20id%20with%20space"
        )
        assert resp.headers["cache-control"] == "no-store"

    def test_import_run_does_not_redirect(self, client: TestClient, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            target_id = create_target(conn, type="server", name="import-target")
            import_run_id = create_run(
                conn,
                module="import",
                name="garak-import",
                source="garak",
                target_id=target_id,
            )
            update_run_status(conn, import_run_id, RunStatus.COMPLETED)
        resp = client.get(f"/runs?run_id={import_run_id}", follow_redirects=False)
        assert resp.status_code == 200


class TestHistoryTargetFilterScope:
    """``query_history_runs`` returns only targets reachable from Run History.

    Targets that exist only because of ``ipi-sweep`` or ``ipi-probe`` runs
    (Intel-only targets) must not appear in the Run History target filter
    dropdown — selecting one would produce an empty result list.
    """

    def _resolve(self, name: str | None) -> str:
        return name or ""

    def test_intel_only_targets_are_filtered_out(self, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            t_workflow = create_target(conn, type="server", name="with-workflow")
            t_sweep_only = create_target(conn, type="server", name="sweep-only")
            t_probe_only = create_target(conn, type="server", name="probe-only")
            create_run(conn, module="workflow", name="assess", target_id=t_workflow)
            create_run(conn, module="ipi-sweep", target_id=t_sweep_only)
            create_run(conn, module="ipi-probe", target_id=t_probe_only)

            result = run_service.query_history_runs(
                conn,
                workflow_filter=None,
                target_filter=None,
                status=None,
                resolve_workflow_display_name=self._resolve,
                resolve_import_display_name=self._resolve,
            )

        target_ids = {t.id for t in result.targets}
        assert target_ids == {t_workflow}

    def test_config_only_target_id_is_included(self, tmp_db: Path) -> None:
        # Workflow run with NULL runs.target_id but config['target_id']
        # set. Mirrors the _effective_target_id precedent in run_service.
        with get_connection(tmp_db) as conn:
            t_config_only = create_target(conn, type="server", name="config-only")
            create_run(
                conn,
                module="workflow",
                name="assess",
                config={"target_id": t_config_only},
            )

            result = run_service.query_history_runs(
                conn,
                workflow_filter=None,
                target_filter=None,
                status=None,
                resolve_workflow_display_name=self._resolve,
                resolve_import_display_name=self._resolve,
            )

        target_ids = {t.id for t in result.targets}
        assert t_config_only in target_ids

    def test_import_run_targets_are_included(self, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            t_import = create_target(conn, type="server", name="import-target")
            import_run_id = create_run(
                conn,
                module="import",
                name="garak-import",
                source="garak",
                target_id=t_import,
            )
            update_run_status(conn, import_run_id, RunStatus.COMPLETED)

            result = run_service.query_history_runs(
                conn,
                workflow_filter=None,
                target_filter=None,
                status=None,
                resolve_workflow_display_name=self._resolve,
                resolve_import_display_name=self._resolve,
            )

        target_ids = {t.id for t in result.targets}
        assert t_import in target_ids
