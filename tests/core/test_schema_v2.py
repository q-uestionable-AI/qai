"""Tests for V2 schema migration (audit_scans table)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from q_ai.core.schema import CURRENT_VERSION, V1_INDEXES, V1_TABLES, migrate


class TestSchemaV2:
    def test_current_version_is_4(self) -> None:
        assert CURRENT_VERSION == 4

    def test_audit_scans_table_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "audit_scans" in tables
        conn.close()

    def test_v1_to_v2_upgrade(self, tmp_path: Path) -> None:
        """Simulate a V1 database that gets upgraded to V2."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")

        # Manually apply V1 migration
        conn.executescript(V1_TABLES)
        conn.executescript(V1_INDEXES)
        conn.execute("PRAGMA user_version = 1")

        # Verify V1 state
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 1

        # Now run migrate() which should upgrade through V2 to V4
        migrate(conn)

        # Verify final state (migrate goes all the way to CURRENT_VERSION)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 4

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "audit_scans" in tables

        # Verify index exists
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "idx_audit_scans_run_id" in indexes
        conn.close()

    def test_audit_scans_fk_to_runs(self, tmp_path: Path) -> None:
        """Verify the audit_scans FK to runs table works."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)

        # Insert a valid run first
        conn.execute(
            "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
            ("run1", "audit", 0),
        )

        # Insert an audit_scan referencing that run
        conn.execute(
            "INSERT INTO audit_scans (id, run_id, transport) VALUES (?, ?, ?)",
            ("scan1", "run1", "stdio"),
        )
        conn.commit()

        # Verify the row was inserted
        row = conn.execute(
            "SELECT id, run_id, transport FROM audit_scans WHERE id = ?",
            ("scan1",),
        ).fetchone()
        assert row == ("scan1", "run1", "stdio")

        # Verify FK constraint: inserting with invalid run_id should fail
        try:
            conn.execute(
                "INSERT INTO audit_scans (id, run_id, transport) VALUES (?, ?, ?)",
                ("scan2", "nonexistent_run", "sse"),
            )
            conn.commit()
            fk_enforced = False
        except sqlite3.IntegrityError:
            fk_enforced = True

        assert fk_enforced, "FK constraint should prevent invalid run_id"
        conn.close()
