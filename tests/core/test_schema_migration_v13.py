"""Tests for V13 schema migration (ipi_payloads.template_id column)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from q_ai.core.schema import (
    CURRENT_VERSION,
    V1_INDEXES,
    V1_TABLES,
    V6_INDEXES,
    V6_TABLES,
    _migrate_v13,
    migrate,
)


class TestSchemaV13:
    """Schema V13 adds a nullable template_id column to ipi_payloads."""

    def test_current_version_is_13(self) -> None:
        """CURRENT_VERSION reflects the V13 bump."""
        assert CURRENT_VERSION == 13

    def test_fresh_db_has_template_id_column(self, tmp_path: Path) -> None:
        """A freshly migrated database exposes template_id on ipi_payloads."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)

        col_info = {
            row[1]: row for row in conn.execute("PRAGMA table_info(ipi_payloads)").fetchall()
        }
        assert "template_id" in col_info
        assert col_info["template_id"][3] == 0, "template_id must be nullable"
        assert col_info["template_id"][2].upper() == "TEXT"
        conn.close()

    def test_user_version_after_migrate(self, tmp_path: Path) -> None:
        """migrate() advances user_version to CURRENT_VERSION (13)."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        migrate(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 13
        conn.close()

    def test_v13_on_existing_v13_db_is_noop(self, tmp_path: Path) -> None:
        """Re-running _migrate_v13 on an already-migrated DB is idempotent."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        migrate(conn)

        # Re-apply v13; should not raise (column already exists)
        _migrate_v13(conn)

        col_info = {
            row[1]: row for row in conn.execute("PRAGMA table_info(ipi_payloads)").fetchall()
        }
        assert "template_id" in col_info
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 13
        conn.close()

    def test_v12_to_v13_preserves_existing_rows(self, tmp_path: Path) -> None:
        """Migrating a populated v12 DB to v13 keeps rows with template_id=NULL."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")

        # Build a V12 state: create V1 + V6 tables, stop at v12.
        conn.executescript(V1_TABLES)
        conn.executescript(V1_INDEXES)
        conn.executescript(V6_TABLES)
        conn.executescript(V6_INDEXES)
        conn.execute("PRAGMA user_version = 12")
        conn.commit()

        # Confirm template_id does not yet exist.
        pre_cols = {row[1] for row in conn.execute("PRAGMA table_info(ipi_payloads)").fetchall()}
        assert "template_id" not in pre_cols

        # Seed a legacy row (no template_id column yet).
        conn.execute(
            """
            INSERT INTO ipi_payloads (
                id, uuid, token, filename, output_path,
                format, technique, payload_style, payload_type,
                callback_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-1",
                "legacy-uuid",
                "tok",
                "legacy.pdf",
                None,
                "pdf",
                "white_ink",
                "obvious",
                "callback",
                "http://example.com/cb",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()

        # Migrate to v13.
        migrate(conn)

        post_cols = {row[1] for row in conn.execute("PRAGMA table_info(ipi_payloads)").fetchall()}
        assert "template_id" in post_cols

        row = conn.execute(
            "SELECT id, uuid, template_id FROM ipi_payloads WHERE id = ?",
            ("legacy-1",),
        ).fetchone()
        assert row is not None
        assert row[0] == "legacy-1"
        assert row[1] == "legacy-uuid"
        # Pre-existing row has template_id set to NULL by ALTER TABLE.
        assert row[2] is None
        conn.close()

    def test_v13_with_missing_ipi_payloads_is_safe(self, tmp_path: Path) -> None:
        """_migrate_v13 is a no-op (for the ALTER) when ipi_payloads does not exist."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        # Start from an empty DB so no tables exist.
        conn.execute("PRAGMA user_version = 12")

        _migrate_v13(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 13
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "ipi_payloads" not in tables
        conn.close()
