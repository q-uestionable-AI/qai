"""Tests for qai runs delete CLI command."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    get_connection,
)
from q_ai.core.models import Severity

runner = CliRunner()


class TestRunsDelete:
    """Tests for qai runs delete command."""

    def test_runs_delete_with_yes(self, tmp_path: Path) -> None:
        """qai runs delete --yes removes the run."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            rid = create_run(conn, module="audit", name="test-scan")

        result = runner.invoke(app, ["runs", "delete", rid, "--yes", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert f"Deleted run '{rid[:8]}'" in result.output

        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (rid,)).fetchone()[0] == 0

    def test_runs_delete_cascades(self, tmp_path: Path) -> None:
        """qai runs delete --yes removes findings and evidence too."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            rid = create_run(conn, module="audit")
            fid = create_finding(
                conn,
                run_id=rid,
                module="audit",
                category="test",
                severity=Severity.LOW,
                title="f1",
            )
            create_evidence(conn, type="request", finding_id=fid, content="data")

        result = runner.invoke(app, ["runs", "delete", rid, "--yes", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "1 findings" in result.output
        assert "1 evidence" in result.output

        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0

    def test_runs_delete_counts_include_child_runs(self, tmp_path: Path) -> None:
        """Output counts reflect findings/evidence of the full cascade (parent + children)."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            parent_rid = create_run(conn, module="chain")
            child_rid = create_run(conn, module="audit", parent_run_id=parent_rid)
            parent_fid = create_finding(
                conn,
                run_id=parent_rid,
                module="chain",
                category="test",
                severity=Severity.LOW,
                title="parent",
            )
            child_fid = create_finding(
                conn,
                run_id=child_rid,
                module="audit",
                category="test",
                severity=Severity.LOW,
                title="child",
            )
            create_evidence(conn, type="log", finding_id=parent_fid, content="p")
            create_evidence(conn, type="log", finding_id=child_fid, content="c")
            create_evidence(conn, type="log", run_id=child_rid, content="c2")

        result = runner.invoke(
            app, ["runs", "delete", parent_rid, "--yes", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        assert "2 findings" in result.output
        assert "3 evidence" in result.output

    def test_runs_delete_cascades_module_data(self, tmp_path: Path) -> None:
        """qai runs delete removes module-backed rows (e.g. audit_scans)."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            rid = create_run(conn, module="audit")
            conn.execute(
                "INSERT INTO audit_scans (id, run_id, transport, server_name) VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, rid, "stdio", "test-server"),
            )

        result = runner.invoke(app, ["runs", "delete", rid, "--yes", "--db-path", str(db_path)])
        assert result.exit_code == 0

        with get_connection(db_path) as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM audit_scans WHERE run_id = ?", (rid,)
                ).fetchone()[0]
                == 0
            )
            assert conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (rid,)).fetchone()[0] == 0

    def test_runs_delete_removes_session_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """qai runs delete cleans up proxy_sessions.session_file on disk."""
        qai_home = tmp_path / ".qai"
        qai_home.mkdir()
        # Override the module-level data-dir constant — it's evaluated once at import.
        monkeypatch.setattr("q_ai.core.db._QAI_DATA_DIR", qai_home)

        sessions_dir = qai_home / "sessions"
        sessions_dir.mkdir()
        session_file = sessions_dir / "s.jsonl"
        session_file.write_text("{}\n", encoding="utf-8")

        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            rid = create_run(conn, module="proxy")
            conn.execute(
                "INSERT INTO proxy_sessions (id, run_id, transport, session_file) "
                "VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, rid, "stdio", str(session_file)),
            )

        assert session_file.exists()

        result = runner.invoke(app, ["runs", "delete", rid, "--yes", "--db-path", str(db_path)])
        assert result.exit_code == 0

        assert not session_file.exists()
        with get_connection(db_path) as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM proxy_sessions WHERE run_id = ?", (rid,)
                ).fetchone()[0]
                == 0
            )
