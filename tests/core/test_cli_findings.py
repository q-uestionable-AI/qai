"""Tests for qai findings CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import create_finding, create_run, get_connection
from q_ai.core.models import Severity

runner = CliRunner()


class TestFindingsList:
    def test_exits_zero_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path):
            pass
        result = runner.invoke(app, ["findings", "list", "--db-path", str(db_path)])
        assert result.exit_code == 0

    def test_shows_findings(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            rid = create_run(conn, module="audit")
            create_finding(
                conn,
                run_id=rid,
                module="audit",
                category="command_injection",
                severity=Severity.HIGH,
                title="Test injection",
            )
        result = runner.invoke(app, ["findings", "list", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "command_injection" in result.output
        assert "HIGH" in result.output

    def test_severity_filter(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            rid = create_run(conn, module="audit")
            create_finding(
                conn,
                run_id=rid,
                module="audit",
                category="low_thing",
                severity=Severity.LOW,
                title="Low",
            )
            create_finding(
                conn,
                run_id=rid,
                module="audit",
                category="high_thing",
                severity=Severity.HIGH,
                title="High",
            )
        result = runner.invoke(
            app,
            ["findings", "list", "--severity", "HIGH", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "High" in result.output
        assert "Low" not in result.output
