"""Tests for the q-ai root CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestCLIHelp:
    """qai --help shows help text."""

    def test_help_exits_zero(self) -> None:
        """--help exits 0 and shows help text."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Offensive security platform" in result.output

    def test_no_args_shows_help(self) -> None:
        """No args shows help text (no_args_is_help)."""
        result = runner.invoke(app, [])
        assert result.exit_code == 0 or result.exit_code == 2
        assert "Offensive security platform" in result.output


class TestCLIVersion:
    """qai --version shows version string."""

    def test_version_exits_zero(self) -> None:
        """--version exits 0 and prints version."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "qai 0.0.1" in result.output

    def test_version_short_flag(self) -> None:
        """-V exits 0 and prints version."""
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "qai 0.0.1" in result.output
