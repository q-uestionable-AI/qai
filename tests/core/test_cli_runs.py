"""Tests for qai runs CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import create_run, get_connection

runner = CliRunner()


class TestRunsList:
    def test_exits_zero_empty_db(self, tmp_path: Path) -> None:
        db = str(tmp_path / "qai.db")
        # Initialize the DB
        with get_connection(tmp_path / "qai.db"):
            pass
        result = runner.invoke(app, ["runs", "list", "--db-path", db])
        assert result.exit_code == 0

    def test_shows_created_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="audit", name="test-scan")
        result = runner.invoke(app, ["runs", "list", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "audit" in result.output
        assert "test-scan" in result.output

    def test_filter_by_module(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            create_run(conn, module="audit", name="a1")
            create_run(conn, module="inject", name="i1")
        result = runner.invoke(
            app,
            ["runs", "list", "--module", "audit", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "audit" in result.output
        assert "inject" not in result.output
