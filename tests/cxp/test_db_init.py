"""Tests for V7 schema migration (cxp_test_results table)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from q_ai.core.schema import V1_INDEXES, V1_TABLES, migrate


class TestSchemaV7:
    def test_cxp_test_results_table_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "cxp_test_results" in tables
        conn.close()

    def test_cxp_indexes_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        migrate(conn)
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "idx_cxp_test_results_run_id" in indexes
        assert "idx_cxp_test_results_campaign_id" in indexes
        conn.close()

    def test_v6_to_v7_upgrade(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(V1_TABLES)
        conn.executescript(V1_INDEXES)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        migrate(conn)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 7
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "cxp_test_results" in tables
        conn.close()

    def test_cxp_fk_to_runs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        conn.execute(
            "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
            ("run1", "cxp", 0),
        )
        conn.execute(
            """INSERT INTO cxp_test_results
               (id, run_id, campaign_id, technique_id, assistant, trigger_prompt,
                capture_mode, raw_output)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("t1", "run1", "run1", "backdoor-claude-md", "Claude Code", "test", "file", "output"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, run_id FROM cxp_test_results WHERE id = ?", ("t1",)
        ).fetchone()
        assert row == ("t1", "run1")
        try:
            conn.execute(
                """INSERT INTO cxp_test_results
                   (id, run_id, campaign_id, technique_id, assistant, trigger_prompt,
                    capture_mode, raw_output)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("t2", "bad_run", "bad", "t", "a", "p", "file", "o"),
            )
            conn.commit()
            fk_enforced = False
        except sqlite3.IntegrityError:
            fk_enforced = True
        assert fk_enforced
        conn.close()
