"""Tests for the q-ai root CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestCLIHelp:
    """qai --help shows help text."""

    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Security testing for agentic AI" in result.output


class TestCLIVersion:
    """qai --version shows version string."""

    def test_version_exits_zero(self) -> None:
        from q_ai import __version__

        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert f"qai {__version__}" in result.output

    def test_version_short_flag(self) -> None:
        from q_ai import __version__

        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert f"qai {__version__}" in result.output


class TestBareQai:
    """Bare `qai` prints help screen instead of launching server."""

    def test_no_args_prints_help(self) -> None:
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "Quick Start" in result.output

    def test_no_args_shows_ui_hint(self) -> None:
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "qai ui" in result.output

    @patch("q_ai.cli._run_server")
    def test_no_args_does_not_launch_server(self, mock_run: MagicMock) -> None:
        runner.invoke(app, [])
        mock_run.assert_not_called()

    def test_subcommands_still_work(self) -> None:
        result = runner.invoke(app, ["runs", "--help"])
        assert result.exit_code == 0
        assert "runs" in result.output.lower()


class TestQaiUi:
    """qai ui launches the web UI."""

    @patch("q_ai.cli._run_server")
    def test_ui_launches_server(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("q_ai.cli._run_server")
    def test_ui_port_option(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["ui", "--port", "9000"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["port"] == 9000

    @patch("q_ai.cli._run_server")
    def test_ui_no_browser_option(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["ui", "--no-browser"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["no_browser"] is True

    def test_ui_invalid_port_fails(self) -> None:
        result = runner.invoke(app, ["ui", "--port", "99999"])
        assert result.exit_code != 0


class TestGroupedHelp:
    """Root --help shows grouped command panels."""

    def test_help_shows_modules_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Modules" in result.output

    def test_help_shows_start_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Start" in result.output

    def test_help_shows_manage_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Manage" in result.output

    def test_help_shows_audit_in_modules(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "audit" in result.output
