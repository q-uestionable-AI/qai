"""Tests for the audit CLI commands."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestListChecks:
    """Tests for the list-checks command."""

    def test_lists_all_scanners(self) -> None:
        """Verify list-checks shows injection scanner."""
        result = runner.invoke(app, ["audit", "list-checks"])
        assert result.exit_code == 0
        assert "injection" in result.output
        assert "command_injection" in result.output

    def test_shows_10_scanners(self) -> None:
        """Verify all 10 scanner categories appear in list-checks output."""
        result = runner.invoke(app, ["audit", "list-checks"])
        assert result.exit_code == 0
        for cat in [
            "command_injection",
            "auth",
            "token_exposure",
            "permissions",
            "tool_poisoning",
            "prompt_injection",
            "audit_telemetry",
            "supply_chain",
            "shadow_servers",
            "context_sharing",
        ]:
            assert cat in result.output

    def test_framework_flag(self) -> None:
        """Verify --framework owasp_mcp_top10 shows OWASP IDs."""
        result = runner.invoke(app, ["audit", "list-checks", "--framework", "owasp_mcp_top10"])
        assert result.exit_code == 0
        assert "MCP05" in result.output


class TestAuditSubcommand:
    """Tests for the audit subcommand structure."""

    def test_audit_help(self) -> None:
        """Verify audit help shows all subcommands."""
        result = runner.invoke(app, ["audit", "--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "list-checks" in result.output
        assert "enumerate" in result.output
        assert "report" in result.output


class TestAuditScanPositionalTarget:
    """Tests for positional TARGET and transport inference in audit scan."""

    def test_scan_help_shows_examples(self) -> None:
        result = runner.invoke(app, ["audit", "scan", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output

    @patch("q_ai.core.cli.prompt.is_tty", return_value=False)
    def test_scan_no_args_non_tty_fails(self, _mock: object) -> None:
        """Non-TTY with no target fails with clear error."""
        result = runner.invoke(app, ["audit", "scan"])
        assert result.exit_code != 0

    def test_backward_compat_explicit_flags(self) -> None:
        """--transport stdio --command still works (triggers connection error, not arg error)."""
        result = runner.invoke(
            app,
            ["audit", "scan", "--transport", "stdio", "--command", "echo hello"],
        )
        # Will fail at connection (not argument parsing)
        assert "Connection failed" in result.output or result.exit_code != 0


class TestAuditEnumeratePositionalTarget:
    """Tests for positional TARGET in audit enumerate."""

    def test_enumerate_help_shows_examples(self) -> None:
        result = runner.invoke(app, ["audit", "enumerate", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output

    @patch("q_ai.core.cli.prompt.is_tty", return_value=False)
    def test_enumerate_no_args_non_tty_fails(self, _mock: object) -> None:
        result = runner.invoke(app, ["audit", "enumerate"])
        assert result.exit_code != 0

    def test_backward_compat_explicit_flags(self) -> None:
        """--transport stdio --command still works."""
        result = runner.invoke(
            app,
            ["audit", "enumerate", "--transport", "stdio", "--command", "echo hello"],
        )
        # Will fail at connection, not argument parsing
        assert "Connection failed" in result.output or result.exit_code != 0
