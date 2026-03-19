"""Tests for bulk operations: bulk delete, bulk export, comparison, target grouping."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import (
    create_finding,
    create_run,
    create_target,
    get_connection,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity


def _seed_runs(
    tmp_db: Path,
    count: int = 3,
    *,
    target_name: str = "test-target",
    workflow_name: str = "assess",
) -> tuple[str, list[str]]:
    """Create a target and N parent workflow runs. Returns (target_id, run_ids)."""
    with get_connection(tmp_db) as conn:
        target_id = create_target(conn, type="server", name=target_name)
        run_ids: list[str] = []
        for _ in range(count):
            rid = create_run(
                conn,
                module="workflow",
                name=workflow_name,
                target_id=target_id,
                config={"target_id": target_id},
            )
            update_run_status(conn, rid, RunStatus.COMPLETED)
            run_ids.append(rid)
    return target_id, run_ids


class TestBulkDelete:
    """Tests for DELETE /api/runs/bulk."""

    def test_bulk_delete_succeeds(self, client: TestClient, tmp_db: Path) -> None:
        _, run_ids = _seed_runs(tmp_db, count=3)
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            json={"run_ids": run_ids},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 3
        assert data["failed"] == []
        # Verify runs are gone
        with get_connection(tmp_db) as conn:
            for rid in run_ids:
                row = conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (rid,)).fetchone()
                assert row[0] == 0

    def test_bulk_delete_partial_failure(self, client: TestClient, tmp_db: Path) -> None:
        _, run_ids = _seed_runs(tmp_db, count=2)
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            json={"run_ids": [*run_ids, "nonexistent-id"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 2
        assert "nonexistent-id" in data["failed"]

    def test_bulk_delete_empty_list_rejected(self, client: TestClient) -> None:
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            json={"run_ids": []},
        )
        assert resp.status_code == 422

    def test_bulk_delete_exceeds_limit(self, client: TestClient) -> None:
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            json={"run_ids": [f"fake-{i}" for i in range(51)]},
        )
        assert resp.status_code == 400
        assert "50" in resp.json()["detail"]

    def test_bulk_delete_invalid_json(self, client: TestClient) -> None:
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_bulk_delete_non_dict_body(self, client: TestClient) -> None:
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            json=["id1", "id2"],
        )
        assert resp.status_code == 422
        assert "JSON object" in resp.json()["detail"]

    def test_bulk_delete_non_string_ids(self, client: TestClient) -> None:
        resp = client.request(
            "DELETE",
            "/api/runs/bulk",
            json={"run_ids": [1, 2, 3]},
        )
        assert resp.status_code == 422
        assert "string" in resp.json()["detail"]


class TestBulkExport:
    """Tests for POST /api/runs/bulk-export."""

    def test_bulk_export_returns_zip(self, client: TestClient, tmp_db: Path) -> None:
        _, run_ids = _seed_runs(tmp_db, count=2)
        resp = client.post(
            "/api/runs/bulk-export",
            json={"run_ids": run_ids},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers.get("content-disposition", "")

        # Verify ZIP contents
        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert len(names) == 2
            for name in names:
                assert name.endswith("_assess.json")
                data = json.loads(zf.read(name))
                assert data["schema_version"] == "run-bundle-v1"

    def test_bulk_export_empty_list_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/runs/bulk-export",
            json={"run_ids": []},
        )
        assert resp.status_code == 422

    def test_bulk_export_exceeds_limit(self, client: TestClient) -> None:
        resp = client.post(
            "/api/runs/bulk-export",
            json={"run_ids": [f"fake-{i}" for i in range(51)]},
        )
        assert resp.status_code == 400

    def test_bulk_export_all_invalid_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/runs/bulk-export",
            json={"run_ids": ["nonexistent"]},
        )
        assert resp.status_code == 404

    def test_bulk_export_non_dict_body(self, client: TestClient) -> None:
        resp = client.post("/api/runs/bulk-export", json=[1, 2])
        assert resp.status_code == 422

    def test_bulk_export_non_string_ids(self, client: TestClient) -> None:
        resp = client.post("/api/runs/bulk-export", json={"run_ids": [123]})
        assert resp.status_code == 422

    def test_bulk_export_skips_invalid_includes_valid(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        _, run_ids = _seed_runs(tmp_db, count=1)
        resp = client.post(
            "/api/runs/bulk-export",
            json={"run_ids": [*run_ids, "nonexistent"]},
        )
        assert resp.status_code == 200
        buf = BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            assert len(zf.namelist()) == 1


class TestRunComparison:
    """Tests for GET /runs/compare."""

    def test_compare_two_runs(self, client: TestClient, tmp_db: Path) -> None:
        _, run_ids = _seed_runs(tmp_db, count=2)
        resp = client.get(f"/runs/compare?left={run_ids[0]}&right={run_ids[1]}")
        assert resp.status_code == 200
        assert "Run Comparison" in resp.text
        assert "Module Coverage" in resp.text
        assert "Finding Diff" in resp.text

    def test_compare_nonexistent_run_404(self, client: TestClient, tmp_db: Path) -> None:
        _, run_ids = _seed_runs(tmp_db, count=1)
        resp = client.get(f"/runs/compare?left={run_ids[0]}&right=nonexistent")
        assert resp.status_code == 404

    def test_compare_shows_finding_diff(self, client: TestClient, tmp_db: Path) -> None:
        """Findings unique to each run appear in left-only / right-only sections."""
        with get_connection(tmp_db) as conn:
            target_id = create_target(conn, type="server", name="cmp-target")
            r1 = create_run(conn, module="workflow", name="assess", target_id=target_id)
            r2 = create_run(conn, module="workflow", name="assess", target_id=target_id)
            update_run_status(conn, r1, RunStatus.COMPLETED)
            update_run_status(conn, r2, RunStatus.COMPLETED)
            # Shared finding
            create_finding(
                conn,
                run_id=r1,
                module="audit",
                category="info_leak",
                severity=Severity.MEDIUM,
                title="Shared Finding",
            )
            create_finding(
                conn,
                run_id=r2,
                module="audit",
                category="info_leak",
                severity=Severity.MEDIUM,
                title="Shared Finding",
            )
            # Left-only finding
            create_finding(
                conn,
                run_id=r1,
                module="audit",
                category="cmd_injection",
                severity=Severity.HIGH,
                title="Left Only Finding",
            )
            # Right-only finding
            create_finding(
                conn,
                run_id=r2,
                module="inject",
                category="tool_poison",
                severity=Severity.CRITICAL,
                title="Right Only Finding",
            )

        resp = client.get(f"/runs/compare?left={r1}&right={r2}")
        assert resp.status_code == 200
        assert "Left Only" in resp.text
        assert "Left Only Finding" in resp.text
        assert "Right Only" in resp.text
        assert "Right Only Finding" in resp.text
        assert "Common" in resp.text
        assert "Shared Finding" in resp.text

    def test_compare_severity_change_not_common(self, client: TestClient, tmp_db: Path) -> None:
        """Same title+module+category but different severity should not be common."""
        with get_connection(tmp_db) as conn:
            target_id = create_target(conn, type="server", name="sev-target")
            r1 = create_run(conn, module="workflow", name="assess", target_id=target_id)
            r2 = create_run(conn, module="workflow", name="assess", target_id=target_id)
            update_run_status(conn, r1, RunStatus.COMPLETED)
            update_run_status(conn, r2, RunStatus.COMPLETED)
            create_finding(
                conn,
                run_id=r1,
                module="audit",
                category="info_leak",
                severity=Severity.LOW,
                title="Sev Change",
            )
            create_finding(
                conn,
                run_id=r2,
                module="audit",
                category="info_leak",
                severity=Severity.HIGH,
                title="Sev Change",
            )

        resp = client.get(f"/runs/compare?left={r1}&right={r2}")
        assert resp.status_code == 200
        # Both should appear as non-common since severity differs
        assert "Left Only" in resp.text
        assert "Right Only" in resp.text


class TestTargetGrouping:
    """Tests for the group_by_target parameter on /api/runs/history."""

    def test_history_with_grouping_returns_200(self, client: TestClient, tmp_db: Path) -> None:
        _seed_runs(tmp_db, count=2, target_name="grp-target")
        resp = client.get("/api/runs/history?group_by_target=1")
        assert resp.status_code == 200

    def test_history_without_grouping_returns_200(self, client: TestClient, tmp_db: Path) -> None:
        _seed_runs(tmp_db, count=2)
        resp = client.get("/api/runs/history")
        assert resp.status_code == 200

    def test_history_grouping_shows_target_name(self, client: TestClient, tmp_db: Path) -> None:
        _seed_runs(tmp_db, count=2, target_name="My Target Server")
        resp = client.get("/api/runs/history?group_by_target=1")
        assert resp.status_code == 200
        assert "My Target Server" in resp.text


class TestRunHistoryCheckboxes:
    """Tests that the run history table includes checkbox elements."""

    def test_table_has_select_all_checkbox(self, client: TestClient, tmp_db: Path) -> None:
        _seed_runs(tmp_db, count=1)
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert 'id="select-all-runs"' in resp.text

    def test_table_rows_have_checkboxes(self, client: TestClient, tmp_db: Path) -> None:
        _seed_runs(tmp_db, count=1)
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert "run-select-cb" in resp.text

    def test_bulk_action_bar_present(self, client: TestClient, tmp_db: Path) -> None:
        _seed_runs(tmp_db, count=1)
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert 'id="bulk-action-bar"' in resp.text
        assert "Delete Selected" in resp.text
        assert "Export Selected" in resp.text
