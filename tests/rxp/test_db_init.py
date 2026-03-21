"""Tests for V8 schema migration (rxp_validations table)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from q_ai.core.schema import V1_INDEXES, V1_TABLES, migrate


class TestSchemaV8:
    """Verify rxp_validations table exists after migration."""

    def test_rxp_validations_table_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "rxp_validations" in tables
        conn.close()

    def test_rxp_indexes_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        migrate(conn)
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "idx_rxp_validations_run_id" in indexes
        assert "idx_rxp_validations_model_id" in indexes
        conn.close()

    def test_v7_to_v8_upgrade(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(V1_TABLES)
        conn.executescript(V1_INDEXES)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        migrate(conn)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 10
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "rxp_validations" in tables
        conn.close()

    def test_rxp_fk_to_runs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        conn.execute(
            "INSERT INTO runs (id, module, status) VALUES (?, ?, ?)",
            ("run1", "rxp", 0),
        )
        conn.execute(
            """INSERT INTO rxp_validations
               (id, run_id, model_id, total_queries, poison_retrievals,
                retrieval_rate, top_k)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("v1", "run1", "minilm-l6", 5, 3, 0.6, 5),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, run_id FROM rxp_validations WHERE id = ?", ("v1",)
        ).fetchone()
        assert row == ("v1", "run1")
        try:
            conn.execute(
                """INSERT INTO rxp_validations
                   (id, run_id, model_id, total_queries, poison_retrievals,
                    retrieval_rate, top_k)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("v2", "bad_run", "minilm-l6", 1, 0, 0.0, 5),
            )
            conn.commit()
            fk_enforced = False
        except sqlite3.IntegrityError:
            fk_enforced = True
        assert fk_enforced
        conn.close()

    def test_rxp_validations_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        migrate(conn)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(rxp_validations)").fetchall()
        }
        expected = {
            "id",
            "run_id",
            "model_id",
            "profile_id",
            "total_queries",
            "poison_retrievals",
            "retrieval_rate",
            "mean_poison_rank",
            "top_k",
            "results_json",
            "created_at",
        }
        assert expected.issubset(columns)
        conn.close()
