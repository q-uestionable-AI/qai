"""Tests for chain detection rule generation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from q_ai.chain.cli import app
from q_ai.chain.detection import generate_detection_rules, write_detection_rules

runner = CliRunner()


def _make_result(
    step_outputs: list[dict] | None = None,
    trust_boundaries: list[str] | None = None,
    success: bool = True,
) -> dict:
    """Build a minimal chain result dict for testing."""
    return {
        "chain_id": "test-chain",
        "chain_name": "Test Chain",
        "target_config": {"audit_transport": "stdio"},
        "step_outputs": step_outputs or [],
        "trust_boundaries_crossed": trust_boundaries or [],
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "dry_run": False,
        "success": success,
    }


def _successful_audit_step(
    step_id: str = "scan-injection",
    technique: str = "injection",
) -> dict:
    """Build a successful audit step output dict."""
    return {
        "step_id": step_id,
        "module": "audit",
        "technique": technique,
        "success": True,
        "status": "success",
        "artifacts": {
            "vulnerable_tool": "get_weather",
            "vulnerability_type": "MCP05",
            "finding_count": "3",
        },
    }


def _successful_inject_step(
    step_id: str = "poison-tool",
    technique: str = "description_poisoning",
) -> dict:
    """Build a successful inject step output dict."""
    return {
        "step_id": step_id,
        "module": "inject",
        "technique": technique,
        "success": True,
        "status": "success",
        "artifacts": {
            "best_outcome": "full_compliance",
            "working_payload": "exfil_via_important_tag",
            "working_technique": "description_poisoning",
            "compliance_rate": "75",
        },
    }


def _failed_step(step_id: str = "failed-step", module: str = "inject") -> dict:
    """Build a failed step output dict."""
    return {
        "step_id": step_id,
        "module": module,
        "technique": "output_injection",
        "success": False,
        "status": "failed",
        "artifacts": {},
        "error": "Campaign failed: model refused",
    }


class TestSigmaRuleGeneration:
    """Tests for Sigma rule generation."""

    def test_audit_step_produces_sigma_rule(self) -> None:
        """A successful audit step produces a valid Sigma rule."""
        result = _make_result(step_outputs=[_successful_audit_step()])
        rules = generate_detection_rules(result, format="sigma")

        assert len(rules) == 1
        rule = rules[0]
        assert "title: MCP Command Execution Detected - get_weather" in rule
        assert "status: experimental" in rule
        assert "level: high" in rule
        assert "product: mcp-server" in rule
        assert "tool_name: get_weather" in rule
        assert "technique: injection" in rule
        assert "attack.execution" in rule

    def test_inject_step_produces_sigma_rule(self) -> None:
        """A successful inject step produces a valid Sigma rule."""
        result = _make_result(step_outputs=[_successful_inject_step()])
        rules = generate_detection_rules(result, format="sigma")

        assert len(rules) == 1
        rule = rules[0]
        assert "title: MCP Tool Poisoning Detected - description_poisoning" in rule
        assert "level: critical" in rule
        assert "product: mcp-agent" in rule

    def test_failed_steps_produce_no_rules(self) -> None:
        """Failed steps are skipped — no rules generated."""
        result = _make_result(step_outputs=[_failed_step()])
        rules = generate_detection_rules(result, format="sigma")
        assert len(rules) == 0

    def test_empty_chain_result(self) -> None:
        """Empty chain result produces no rules."""
        result = _make_result(step_outputs=[])
        rules = generate_detection_rules(result, format="sigma")
        assert len(rules) == 0


class TestWazuhRuleGeneration:
    """Tests for Wazuh rule generation."""

    def test_audit_step_produces_wazuh_rule(self) -> None:
        """A successful audit step produces a valid Wazuh XML rule."""
        result = _make_result(step_outputs=[_successful_audit_step()])
        rules = generate_detection_rules(result, format="wazuh")

        assert len(rules) == 1
        rule = rules[0]
        assert "<rule id=" in rule
        assert 'level="10"' in rule
        assert "get_weather" in rule
        assert "injection" in rule
        assert "<mitre>" in rule
        assert "</rule>" in rule

    def test_inject_step_produces_wazuh_rule(self) -> None:
        """A successful inject step produces a valid Wazuh XML rule."""
        result = _make_result(step_outputs=[_successful_inject_step()])
        rules = generate_detection_rules(result, format="wazuh")

        assert len(rules) == 1
        rule = rules[0]
        assert "<rule id=" in rule
        assert 'level="12"' in rule
        assert "description_poisoning" in rule
        assert "full_compliance" in rule

    def test_wazuh_group_name_is_qai(self) -> None:
        """Wazuh rules use 'qai' group name, not 'counteragent'."""
        result = _make_result(step_outputs=[_successful_audit_step()])
        rules = generate_detection_rules(result, format="wazuh")
        assert "qai" in rules[0]
        assert "counteragent" not in rules[0]


class TestWriteDetectionRules:
    """Tests for write_detection_rules function."""

    def test_write_sigma_to_file(self, tmp_path: Path) -> None:
        """Writing Sigma rules to a file concatenates with ---."""
        result = _make_result(step_outputs=[_successful_audit_step(), _successful_inject_step()])
        rules = generate_detection_rules(result, format="sigma")
        out = tmp_path / "rules.yml"
        write_detection_rules(rules, out, format="sigma")

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "---" in content

    def test_write_wazuh_to_file(self, tmp_path: Path) -> None:
        """Writing Wazuh rules to a file wraps in <group>."""
        result = _make_result(step_outputs=[_successful_audit_step(), _successful_inject_step()])
        rules = generate_detection_rules(result, format="wazuh")
        out = tmp_path / "rules.xml"
        write_detection_rules(rules, out, format="wazuh")

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert '<group name="qai">' in content
        assert "counteragent" not in content
        assert "</group>" in content

    def test_write_to_directory(self, tmp_path: Path) -> None:
        """Writing to a directory creates one file per rule."""
        result = _make_result(step_outputs=[_successful_audit_step(), _successful_inject_step()])
        rules = generate_detection_rules(result, format="sigma")
        out_dir = tmp_path / "rules_dir"
        write_detection_rules(rules, out_dir, format="sigma")

        assert out_dir.is_dir()
        files = sorted(out_dir.iterdir())
        assert len(files) == 2
        assert files[0].suffix == ".yml"


class TestDetectCLI:
    """Tests for chain detect CLI command."""

    def test_help(self) -> None:
        """Help text includes --results option."""
        result = runner.invoke(app, ["detect", "--help"])
        assert result.exit_code == 0
        assert "--results" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)

    def test_sigma_stdout(self, tmp_path: Path) -> None:
        """Without --output, prints Sigma rules to stdout."""
        data = _make_result(step_outputs=[_successful_audit_step()])
        infile = tmp_path / "result.json"
        infile.write_text(json.dumps(data), encoding="utf-8")

        result = runner.invoke(app, ["detect", "--results", str(infile)])
        assert result.exit_code == 0
        assert "MCP Command Execution Detected" in result.output

    def test_empty_results_no_rules(self, tmp_path: Path) -> None:
        """Empty chain result with no successful steps exits cleanly."""
        data = _make_result(step_outputs=[_failed_step()])
        infile = tmp_path / "result.json"
        infile.write_text(json.dumps(data), encoding="utf-8")

        result = runner.invoke(app, ["detect", "--results", str(infile)])
        assert result.exit_code == 0
        assert "no" in result.output.lower()
