"""Tests for database management API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import (
    create_run,
    create_target,
    get_connection,
)


def _seed_target(db_path: Path) -> str:
    """Insert a target and return its ID."""
    with get_connection(db_path) as conn:
        return create_target(conn, type="server", name="Test Server", uri="http://localhost")


def _seed_run(db_path: Path, target_id: str | None = None) -> str:
    """Insert a run and return its ID."""
    with get_connection(db_path) as conn:
        return create_run(conn, module="audit", name="test-scan", target_id=target_id)


class TestDeleteTargetAPI:
    def test_delete_target_api(self, client: TestClient, tmp_db: Path) -> None:
        """DELETE /api/targets/{id} removes the target."""
        tid = _seed_target(tmp_db)
        resp = client.delete(f"/api/targets/{tid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["orphaned_runs"] == 0

    def test_delete_target_not_found(self, client: TestClient) -> None:
        """DELETE /api/targets/{id} returns 404 for missing target."""
        resp = client.delete("/api/targets/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestDeleteRunAPI:
    def test_delete_run_api(self, client: TestClient, tmp_db: Path) -> None:
        """DELETE /api/runs/{id} removes the run."""
        rid = _seed_run(tmp_db)
        resp = client.delete(f"/api/runs/{rid}")
        assert resp.status_code == 200

    def test_delete_run_not_found(self, client: TestClient) -> None:
        """DELETE /api/runs/{id} returns 404 for missing run."""
        resp = client.delete("/api/runs/nonexistent")
        assert resp.status_code == 404


class TestBackupAPI:
    def test_backup_api(self, client: TestClient, tmp_db: Path) -> None:
        """POST /api/db/backup creates a backup file."""
        resp = client.post("/api/db/backup")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["path"]
        # Clean up backup file
        backup = Path(data["path"])
        backup.unlink(missing_ok=True)


class TestResetAPI:
    def test_reset_api(self, client: TestClient, tmp_db: Path) -> None:
        """POST /api/db/reset clears data and returns backup path."""
        tid = _seed_target(tmp_db)
        _seed_run(tmp_db, target_id=tid)

        resp = client.post("/api/db/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reset"
        assert data["backup_path"]

        # Verify data was cleared
        with get_connection(tmp_db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

        # Clean up backup file
        backup = Path(data["backup_path"])
        backup.unlink(missing_ok=True)
