"""Tests for q_ai inject CLI subcommands."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from q_ai.inject.cli import _load_config_names, app, console

runner = CliRunner()

# Set console width wide enough for Rich table columns to avoid truncation.
console.width = 200


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestInjectHelp:
    """inject --help shows all subcommands."""

    def test_inject_help(self) -> None:
        """All four subcommands appear in help output."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "serve" in plain
        assert "campaign" in plain
        assert "list-payloads" in plain
        assert "report" in plain


class TestServe:
    """inject serve subcommand."""

    def test_serve_help(self) -> None:
        """Help text includes all serve options."""
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "--transport" in plain
        assert "--port" in plain
        assert "--payload-dir" in plain
        assert "--config" in plain

    def test_serve_invalid_transport(self) -> None:
        """Invalid transport value exits with error."""
        result = runner.invoke(app, ["serve", "--transport", "invalid"])
        assert result.exit_code != 0
        plain = _strip_ansi(result.output)
        assert "invalid" in plain.lower()

    def test_serve_stdio_calls_build_and_run(self) -> None:
        """Serve with stdio wires build_server and server.run correctly."""
        mock_server = MagicMock()
        with patch("q_ai.inject.cli.build_server", return_value=mock_server):
            result = runner.invoke(app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 0
        mock_server.run.assert_called_once_with(transport="stdio")


class TestLoadConfigNames:
    """Tests for _load_config_names helper."""

    def test_valid_config(self, tmp_path: Path) -> None:
        """Loads a YAML list of strings."""
        config = tmp_path / "config.yaml"
        config.write_text("- exfil_via_important_tag\n- shadow_tool\n", encoding="utf-8")
        result = _load_config_names(config)
        assert result == ["exfil_via_important_tag", "shadow_tool"]

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Raises BadParameter for missing file."""
        import typer

        with pytest.raises(typer.BadParameter, match="Cannot read config file"):
            _load_config_names(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_structure(self, tmp_path: Path) -> None:
        """Raises BadParameter when YAML is not a list of strings."""
        config = tmp_path / "config.yaml"
        config.write_text("key: value\n", encoding="utf-8")
        import typer

        with pytest.raises(typer.BadParameter, match="YAML list of payload name strings"):
            _load_config_names(config)

    def test_list_of_non_strings(self, tmp_path: Path) -> None:
        """Raises BadParameter when YAML list contains non-strings."""
        config = tmp_path / "config.yaml"
        config.write_text("- 1\n- 2\n", encoding="utf-8")
        import typer

        with pytest.raises(typer.BadParameter, match="YAML list of payload name strings"):
            _load_config_names(config)


class TestCampaign:
    """inject campaign subcommand."""

    def test_campaign_help(self) -> None:
        """Help text includes all campaign options."""
        result = runner.invoke(app, ["campaign", "--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "--model" in plain
        assert "--rounds" in plain
        assert "--output" in plain
        assert "--payloads" in plain
        assert "--technique" in plain
        assert "--target" in plain

    def test_campaign_no_model_no_env_var_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Campaign with no --model and no QAI_MODEL exits with error."""
        monkeypatch.delenv("QAI_MODEL", raising=False)
        result = runner.invoke(app, ["campaign"])
        assert result.exit_code == 1
        plain = _strip_ansi(result.output)
        assert "No model specified" in plain

    def test_campaign_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Campaign uses QAI_MODEL env var when --model not provided."""
        monkeypatch.setenv("QAI_MODEL", "test-env-model")
        # Use nonexistent payload to avoid API call — just verify model resolves
        result = runner.invoke(app, ["campaign", "--payloads", "nonexistent_payload"])
        assert result.exit_code == 1
        plain = _strip_ansi(result.output)
        # Should get "No payloads matched" not "No model specified"
        assert "No payloads matched" in plain

    def test_campaign_no_matching_payloads(self) -> None:
        """Campaign with no matching payloads exits 1."""
        result = runner.invoke(
            app, ["campaign", "--model", "test-model", "--payloads", "nonexistent_payload"]
        )
        assert result.exit_code == 1
        plain = _strip_ansi(result.output)
        assert "No payloads matched" in plain


class TestListPayloads:
    """inject list-payloads subcommand."""

    def test_list_payloads_help(self) -> None:
        """Help text includes all list-payloads options."""
        result = runner.invoke(app, ["list-payloads", "--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "--technique" in plain
        assert "--target" in plain

    def test_list_payloads_exits_zero(self) -> None:
        result = runner.invoke(app, ["list-payloads"])
        assert result.exit_code == 0

    def test_list_payloads_shows_all_payloads(self) -> None:
        result = runner.invoke(app, ["list-payloads"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "exfil_via_important_tag" in plain
        assert "output_instruction_injection" in plain
        assert "shadow_tool" in plain

    def test_list_payloads_filter_description_poisoning(self) -> None:
        result = runner.invoke(app, ["list-payloads", "--technique", "description_poisoning"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "description_poisoning" in plain
        assert "output_injection" not in plain
        # Count payload rows by known names
        dp_names = [
            "exfil_via_important_tag",
            "preference_manipulation",
            "concealment_directive",
            "role_reassignment",
            "hidden_unicode_instruction",
            "long_description_buried",
        ]
        for name in dp_names:
            assert name in plain, f"Missing payload: {name}"

    def test_list_payloads_filter_output_injection(self) -> None:
        result = runner.invoke(app, ["list-payloads", "--technique", "output_injection"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        oi_names = [
            "output_instruction_injection",
            "output_url_exfil",
            "output_tool_call_injection",
            "output_markdown_injection",
        ]
        for name in oi_names:
            assert name in plain, f"Missing payload: {name}"

    def test_list_payloads_filter_cross_tool(self) -> None:
        result = runner.invoke(app, ["list-payloads", "--technique", "cross_tool_escalation"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        ct_names = ["shadow_tool", "chain_via_description", "parameter_exfil"]
        for name in ct_names:
            assert name in plain, f"Missing payload: {name}"

    def test_list_payloads_invalid_technique(self) -> None:
        result = runner.invoke(app, ["list-payloads", "--technique", "nonexistent"])
        assert result.exit_code != 0
        plain = _strip_ansi(result.output)
        assert "Unknown technique" in plain


class TestReport:
    """inject report subcommand."""

    def test_report_help(self) -> None:
        """Help text includes all report options."""
        result = runner.invoke(app, ["report", "--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "--input" in plain
        assert "--format" in plain

    def test_report_missing_file(self) -> None:
        """Report with nonexistent input exits 1."""
        result = runner.invoke(app, ["report", "--input", "nonexistent.json"])
        assert result.exit_code == 1
        plain = _strip_ansi(result.output)
        assert "not found" in plain.lower()

    def test_report_table_format(self, tmp_path: Path) -> None:
        """Report renders a Rich table from valid campaign JSON."""
        import json

        campaign_data = {
            "id": "campaign-test",
            "name": "test",
            "model": "test-model",
            "started_at": "2026-03-03T00:00:00+00:00",
            "finished_at": "2026-03-03T00:01:00+00:00",
            "summary": {"total": 1, "full_compliance": 1},
            "results": [
                {
                    "payload_name": "test_payload",
                    "technique": "description_poisoning",
                    "outcome": "full_compliance",
                    "evidence": "[]",
                    "target_agent": "test-model",
                    "timestamp": "2026-03-03T00:00:30+00:00",
                }
            ],
        }
        json_file = tmp_path / "campaign.json"
        json_file.write_text(json.dumps(campaign_data))

        result = runner.invoke(app, ["report", "--input", str(json_file)])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "test_payload" in plain
        assert "full_compliance" in plain

    def test_report_json_format(self, tmp_path: Path) -> None:
        """Report with --format json outputs raw JSON."""
        import json

        campaign_data = {
            "id": "campaign-test",
            "name": "test",
            "model": "test-model",
            "started_at": "2026-03-03T00:00:00+00:00",
            "finished_at": None,
            "summary": {"total": 0},
            "results": [],
        }
        json_file = tmp_path / "campaign.json"
        json_file.write_text(json.dumps(campaign_data))

        result = runner.invoke(app, ["report", "--input", str(json_file), "--format", "json"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "campaign-test" in plain
