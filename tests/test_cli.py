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
        assert "Offensive security platform" in result.output

    @patch("q_ai.cli._run_server")
    def test_port_option_accepted(self, mock_run: MagicMock) -> None:
        """--port is accepted and passed through to the server."""
        result = runner.invoke(app, ["--port", "9000"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["port"] == 9000

    @patch("q_ai.cli._run_server")
    def test_no_browser_option_accepted(self, mock_run: MagicMock) -> None:
        """--no-browser is accepted and passed through to the server."""
        result = runner.invoke(app, ["--no-browser"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["no_browser"] is True


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


class TestCLIServerLaunch:
    """qai with no subcommand launches the web server."""

    @patch("q_ai.cli._run_server")
    def test_no_args_launches_server(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    @patch("q_ai.cli._run_server")
    def test_no_browser_flag(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["--no-browser"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["no_browser"] is True

    @patch("q_ai.cli._run_server")
    def test_port_flag(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["--port", "9000"])
        assert result.exit_code == 0

    @patch("q_ai.cli._run_server")
    def test_invalid_port_fails(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["--port", "99999"])
        assert result.exit_code != 0

    @patch("q_ai.cli._run_server")
    def test_subcommands_still_work(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["runs", "--help"])
        assert result.exit_code == 0
        assert "runs" in result.output.lower()
        mock_run.assert_not_called()
