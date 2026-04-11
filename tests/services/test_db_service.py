"""Tests for the database management service."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from q_ai.core.db import create_evidence, create_finding, create_run, create_target
from q_ai.core.models import Severity
from q_ai.services.db_service import (
    backup_database,
    delete_run,
    delete_target,
    reset_database,
)


class TestBackupDatabase:
    """Tests for backup_database service function."""

    def test_backup_database_default_path(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Backup with no output_path creates a timestamped file in ~/.qai/backups/."""
        db_file = tmp_path / "test.db"
        result = backup_database(db_file)
        assert result.exists()
        assert result.parent.name == "backups"
        assert result.name.startswith("qai-")
        assert result.suffix == ".db"
        # Clean up
        result.unlink(missing_ok=True)

    def test_backup_database_custom_path(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Backup with explicit output_path writes to that location."""
        db_file = tmp_path / "test.db"
        dest = tmp_path / "my-backup.db"
        result = backup_database(db_file, output_path=dest)
        assert result == dest
        assert dest.exists()
        assert dest.stat().st_size == db_file.stat().st_size


class TestResetDatabase:
    """Tests for reset_database service function."""

    def test_reset_database_clears_data(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Reset deletes all records from operational tables."""
        target_id = create_target(db, type="server", name="t1", uri="http://x")
        run_id = create_run(db, module="audit", target_id=target_id)
        create_finding(
            db,
            run_id=run_id,
            module="audit",
            category="test",
            severity=Severity.LOW,
            title="f1",
        )
        db.commit()

        db_file = tmp_path / "test.db"
        reset_database(db, db_file, auto_backup=False)

        assert db.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0

    def test_reset_database_preserves_settings(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Reset leaves the settings table untouched."""
        db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("theme", "dark", "2025-01-01T00:00:00"),
        )
        db.commit()

        db_file = tmp_path / "test.db"
        reset_database(db, db_file, auto_backup=False)

        row = db.execute("SELECT value FROM settings WHERE key = 'theme'").fetchone()
        assert row is not None
        assert row[0] == "dark"

    def test_reset_database_auto_backup(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        """Reset with auto_backup=True returns a backup path."""
        db_file = tmp_path / "test.db"
        result = reset_database(db, db_file, auto_backup=True)
        assert result is not None
        assert result.exists()
        # Clean up
        result.unlink(missing_ok=True)


class TestDeleteTarget:
    """Tests for delete_target service function."""

    def test_delete_target_orphans_runs(self, db: sqlite3.Connection) -> None:
        """Deleting a target nullifies target_id on associated runs."""
        target_id = create_target(db, type="server", name="t1", uri="http://x")
        run1 = create_run(db, module="audit", target_id=target_id)
        run2 = create_run(db, module="inject", target_id=target_id)
        db.commit()

        orphaned = delete_target(db, target_id)

        assert orphaned == 2
        # Target gone
        assert (
            db.execute("SELECT COUNT(*) FROM targets WHERE id = ?", (target_id,)).fetchone()[0] == 0
        )
        # Runs still exist but target_id is NULL
        for rid in (run1, run2):
            row = db.execute("SELECT target_id FROM runs WHERE id = ?", (rid,)).fetchone()
            assert row is not None
            assert row["target_id"] is None

    def test_delete_target_not_found(self, db: sqlite3.Connection) -> None:
        """Deleting a nonexistent target raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            delete_target(db, "nonexistent_id")


class TestDeleteRun:
    """Tests for delete_run service function."""

    def test_delete_run_cascades(self, db: sqlite3.Connection) -> None:
        """Deleting a run removes its findings and evidence."""
        run_id = create_run(db, module="audit")
        fid = create_finding(
            db,
            run_id=run_id,
            module="audit",
            category="test",
            severity=Severity.LOW,
            title="f1",
        )
        create_evidence(db, type="request", finding_id=fid, content="payload")
        create_evidence(db, type="log", run_id=run_id, content="log data")
        db.commit()

        result = delete_run(db, run_id)

        assert result == {"findings_deleted": 1, "evidence_deleted": 2}
        assert db.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM findings WHERE run_id = ?", (run_id,)).fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT COUNT(*) FROM evidence WHERE run_id = ?", (run_id,)).fetchone()[0]
            == 0
        )

    def test_delete_run_not_found(self, db: sqlite3.Connection) -> None:
        """Deleting a nonexistent run raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            delete_run(db, "nonexistent_id")
