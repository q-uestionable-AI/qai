"""Tests for qai targets CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import get_connection

runner = CliRunner()


class TestTargetsList:
    def test_exits_zero_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        with get_connection(db_path):
            pass
        result = runner.invoke(app, ["targets", "list", "--db-path", str(db_path)])
        assert result.exit_code == 0


class TestTargetsAdd:
    def test_add_target(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        result = runner.invoke(
            app,
            [
                "targets",
                "add",
                "--type",
                "server",
                "--name",
                "test-mcp",
                "--uri",
                "http://localhost",
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert "Created target" in result.output

    def test_add_then_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        runner.invoke(
            app,
            [
                "targets",
                "add",
                "--type",
                "server",
                "--name",
                "visible-target",
                "--uri",
                "http://localhost:8080",
                "--db-path",
                str(db_path),
            ],
        )
        result = runner.invoke(app, ["targets", "list", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "visible-target" in result.output
        assert "http://localhost:8080" in result.output

    def test_add_with_metadata(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        result = runner.invoke(
            app,
            [
                "targets",
                "add",
                "--type",
                "server",
                "--name",
                "meta-target",
                "--metadata",
                '{"transport": "stdio"}',
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 0
