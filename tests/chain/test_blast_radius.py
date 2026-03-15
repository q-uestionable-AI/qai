"""Tests for chain blast-radius analysis and reporting."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from q_ai.chain.blast_radius import (
    analyze_blast_radius,
    write_blast_radius_report,
)
from q_ai.chain.cli import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _make_result(
    step_outputs: list[dict] | None = None,
    trust_boundaries: list[str] | None = None,
    success: bool = True,
    target_config: dict | None = None,
) -> dict:
    """Build a minimal chain result dict for testing."""
    return {
        "chain_id": "test-chain",
        "chain_name": "Test Chain",
        "target_config": target_config or {"audit_transport": "stdio"},
        "step_outputs": step_outputs or [],
        "trust_boundaries_crossed": trust_boundaries or [],
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "dry_run": False,
        "success": success,
    }


def _successful_step(step_id: str = "scan-injection", module: str = "audit") -> dict:
    """Build a successful step output dict."""
    artifacts = {}
    if module == "audit":
        artifacts = {
            "vulnerable_tool": "get_weather",
            "vulnerability_type": "MCP05",
            "finding_count": "3",
            "finding_evidence": "command injection via parameter",
        }
    elif module == "inject":
        artifacts = {
            "best_outcome": "full_compliance",
            "working_payload": "exfil_via_important_tag",
            "working_technique": "description_poisoning",
            "compliance_rate": "75",
        }
    return {
        "step_id": step_id,
        "module": module,
        "technique": "injection" if module == "audit" else "description_poisoning",
        "success": True,
        "status": "success",
        "artifacts": artifacts,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:30+00:00",
        "error": None,
    }


def _failed_step(step_id: str = "poison-tool", module: str = "inject") -> dict:
    """Build a failed step output dict."""
    return {
        "step_id": step_id,
        "module": module,
        "technique": "output_injection",
        "success": False,
        "status": "failed",
        "artifacts": {},
        "started_at": "2026-01-01T00:00:30+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "error": "Campaign failed: model refused",
    }


class TestAnalyzeBlastRadius:
    """Tests for analyze_blast_radius function."""

    def test_successful_chain(self) -> None:
        """All steps succeed — overall_success True, correct counts."""
        result = _make_result(
            step_outputs=[
                _successful_step("scan", "audit"),
                _successful_step("poison", "inject"),
            ],
            trust_boundaries=["client-to-server", "agent-to-tool"],
            success=True,
        )
        analysis = analyze_blast_radius(result)

        assert analysis["chain_id"] == "test-chain"
        assert analysis["chain_name"] == "Test Chain"
        br = analysis["blast_radius"]
        assert br["overall_success"] is True
        assert br["steps_succeeded"] == 2
        assert br["steps_failed"] == 0
        assert br["steps_total"] == 2
        assert br["trust_boundaries_crossed"] == ["client-to-server", "agent-to-tool"]

    def test_mixed_result(self) -> None:
        """Some steps fail — correct success/failure counts."""
        result = _make_result(
            step_outputs=[
                _successful_step("scan", "audit"),
                _failed_step("poison", "inject"),
            ],
            success=False,
        )
        analysis = analyze_blast_radius(result)
        br = analysis["blast_radius"]
        assert br["overall_success"] is False
        assert br["steps_succeeded"] == 1
        assert br["steps_failed"] == 1
        assert br["steps_total"] == 2

    def test_empty_chain(self) -> None:
        """No steps — everything zeroed out."""
        result = _make_result(step_outputs=[], success=False)
        analysis = analyze_blast_radius(result)
        br = analysis["blast_radius"]
        assert br["steps_total"] == 0
        assert br["data_reached"] == []
        assert br["attack_path"] == []
        assert br["systems_touched"] == []

    def test_data_reached_extracts_artifacts(self) -> None:
        """Non-empty artifact values appear in data_reached."""
        result = _make_result(step_outputs=[_successful_step("scan", "audit")])
        analysis = analyze_blast_radius(result)
        types = [d["type"] for d in analysis["blast_radius"]["data_reached"]]
        assert "vulnerable_tool" in types
        assert "vulnerability_type" in types
        assert "finding_count" in types


class TestWriteBlastRadiusReport:
    """Tests for write_blast_radius_report function."""

    def test_json_output(self, tmp_path: Path) -> None:
        """JSON format produces valid parseable JSON."""
        result = _make_result(
            step_outputs=[_successful_step()],
            trust_boundaries=["client-to-server"],
        )
        analysis = analyze_blast_radius(result)
        out = tmp_path / "report.json"
        write_blast_radius_report(analysis, out, fmt="json")

        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["chain_id"] == "test-chain"
        assert "blast_radius" in data

    def test_html_output(self, tmp_path: Path) -> None:
        """HTML format produces a valid HTML document."""
        result = _make_result(
            step_outputs=[_successful_step(), _failed_step()],
            trust_boundaries=["client-to-server", "agent-to-tool"],
        )
        analysis = analyze_blast_radius(result)
        out = tmp_path / "report.html"
        write_blast_radius_report(analysis, out, fmt="html")

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "Blast Radius Report" in content
        assert "Test Chain" in content
        assert "q-ai" in content
        assert "qai" in content


class TestBlastRadiusCLI:
    """Tests for chain blast-radius CLI command."""

    def test_help(self) -> None:
        """Help text includes --results option."""
        result = runner.invoke(app, ["blast-radius", "--help"])
        assert result.exit_code == 0
        assert "--results" in _strip_ansi(result.output)

    def test_missing_file(self, tmp_path: Path) -> None:
        """Nonexistent results file exits 1."""
        missing = tmp_path / "nonexistent.json"
        result = runner.invoke(app, ["blast-radius", "--results", str(missing)])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_json_stdout(self, tmp_path: Path) -> None:
        """Without --output, prints JSON to stdout."""
        data = _make_result(step_outputs=[_successful_step()], success=True)
        infile = tmp_path / "result.json"
        infile.write_text(json.dumps(data), encoding="utf-8")

        result = runner.invoke(app, ["blast-radius", "--results", str(infile)])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["chain_id"] == "test-chain"
        assert "blast_radius" in output
