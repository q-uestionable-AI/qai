"""Tests for IPI schema migration (V7)."""

from __future__ import annotations

from pathlib import Path

from q_ai.core.db import get_connection


class TestIPISchemaV7:
    """Verify ipi_payloads and ipi_hits tables exist after migration."""

    def test_tables_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "ipi_payloads" in tables
            assert "ipi_hits" in tables

    def test_schema_version_is_7(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 9

    def test_ipi_payloads_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(ipi_payloads)").fetchall()
            }
            expected = {
                "id",
                "run_id",
                "uuid",
                "token",
                "filename",
                "output_path",
                "format",
                "technique",
                "payload_style",
                "payload_type",
                "callback_url",
                "created_at",
            }
            assert expected.issubset(columns)

    def test_ipi_hits_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(ipi_hits)").fetchall()
            }
            expected = {
                "id",
                "uuid",
                "source_ip",
                "user_agent",
                "headers",
                "body",
                "token_valid",
                "confidence",
                "timestamp",
            }
            assert expected.issubset(columns)

    def test_indexes_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_ipi_payloads_uuid" in indexes
            assert "idx_ipi_payloads_run_id" in indexes
            assert "idx_ipi_hits_uuid" in indexes

    def test_migration_from_v5(self, tmp_path: Path) -> None:
        """Existing V5 database upgrades through V6→V7, creating IPI tables."""
        import sqlite3

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        conn.close()

        with get_connection(db_path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 9
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "ipi_payloads" in tables
            assert "ipi_hits" in tables
