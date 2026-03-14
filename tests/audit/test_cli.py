"""Tests for the audit CLI commands."""

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
