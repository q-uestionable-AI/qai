"""Tests for qai runs delete CLI command."""

from __future__ import annotations

from pathlib import Path

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
