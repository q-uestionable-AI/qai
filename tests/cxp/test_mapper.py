"""Tests for CXP mapper (runs and findings persistence)."""

from __future__ import annotations

import json
from pathlib import Path

from q_ai.core.db import create_run, get_connection
from q_ai.cxp.mapper import persist_build, persist_test_result


class TestPersistBuild:
    def test_creates_run_record(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        run_id = persist_build("cursorrules", ["weak-crypto-md5"], "/tmp/repo", db_path=db)
        assert len(run_id) == 32
        with get_connection(db) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert row is not None
            assert row["module"] == "cxp"
            assert row["status"] == 2  # COMPLETED

    def test_run_name_contains_format(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        run_id = persist_build("claude-md", ["r1", "r2"], "/tmp/repo", db_path=db)
        with get_connection(db) as conn:
            row = conn.execute("SELECT name FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert "claude-md" in row["name"]
            assert "2" in row["name"]


class TestPersistTestResult:
    def test_hit_creates_finding(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, status, started_at) VALUES (?, ?, ?, ?)",
                ("camp1", "cxp", 0, "2026-01-01T00:00:00"),
            )
        finding_id = persist_test_result(
            "r1", "camp1", "backdoor-claude-md", "Claude Code", "hit", db_path=db
        )
        assert finding_id is not None
        with get_connection(db) as conn:
            row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
            assert row is not None
            assert row["module"] == "cxp"
            assert row["category"] == "context-poisoning"
            assert "backdoor-claude-md" in row["title"]

    def test_miss_no_finding(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        finding_id = persist_test_result(
            "r1", "camp1", "backdoor-claude-md", "Claude Code", "miss", db_path=db
        )
        assert finding_id is None

    def test_partial_no_finding(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        finding_id = persist_test_result(
            "r1", "camp1", "exfil-claude-md", "Claude Code", "partial", db_path=db
        )
        assert finding_id is None


class TestPersistBuildRunId:
    """Tests for persist_build run_id passthrough."""

    def test_persist_build_uses_supplied_run_id(self, tmp_path: Path) -> None:
        """Verify persist_build uses provided run_id instead of creating a new one."""
        db = tmp_path / "test.db"
        # Pre-create a run
        with get_connection(db) as conn:
            pre_run_id = create_run(conn, module="cxp", name="pre-created")

        run_id = persist_build(
            "cursorrules",
            ["weak-crypto-md5"],
            "/tmp/repo",
            db_path=db,
            run_id=pre_run_id,
        )
        assert run_id == pre_run_id

        with get_connection(db) as conn:
            all_runs = conn.execute("SELECT * FROM runs").fetchall()
            assert len(all_runs) == 1
            assert all_runs[0]["id"] == pre_run_id

            # Verify metadata was written to the existing run row
            row = all_runs[0]
            assert row["name"] == "build-cursorrules-1-rules"
            config = json.loads(row["config"])
            assert config["format_id"] == "cursorrules"
            assert config["rules_inserted"] == ["weak-crypto-md5"]
            assert config["repo_dir"] == "/tmp/repo"
