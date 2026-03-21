"""Tests for audit reporting modules."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from q_ai.audit.reporting.json_report import (
    dict_to_finding,
    finding_to_dict,
    generate_json_report,
)
from q_ai.audit.reporting.sarif_report import generate_sarif_report
from q_ai.audit.reporting.severity import severity_from_cvss
from q_ai.mcp.models import ScanFinding, Severity


def _make_finding(**kwargs: Any) -> ScanFinding:
    """Helper to create a ScanFinding with sensible defaults."""
    defaults = {
        "rule_id": "MCP05-001",
        "category": "command_injection",
        "title": "Command injection in tool",
        "description": "Found injection vulnerability",
        "severity": Severity.HIGH,
        "evidence": "payload response",
        "remediation": "sanitize inputs",
        "tool_name": "exec_tool",
        "metadata": {"payload": "test"},
        "framework_ids": {"owasp_mcp_top10": "MCP05"},
    }
    defaults.update(kwargs)
    return ScanFinding(**defaults)


@dataclass
class FakeScanResult:
    """Minimal duck-typed ScanResult for testing."""

    findings: list[ScanFinding] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)
    tools_scanned: int = 0
    scanners_run: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    errors: list[dict[str, str]] = field(default_factory=list)


class TestFindingSerialization:
    def test_roundtrip(self) -> None:
        """Verify finding -> dict -> finding roundtrip preserves data."""
        original = _make_finding()
        d = finding_to_dict(original)
        restored = dict_to_finding(d)

        assert restored.rule_id == original.rule_id
        assert restored.category == original.category
        assert restored.title == original.title
        assert restored.description == original.description
        assert restored.severity == original.severity
        assert restored.evidence == original.evidence
        assert restored.remediation == original.remediation
        assert restored.tool_name == original.tool_name
        assert restored.metadata == original.metadata
        assert restored.framework_ids == original.framework_ids

    def test_finding_to_dict_has_backward_compat_owasp_id(self) -> None:
        """Verify finding_to_dict includes owasp_id for backward compat."""
        finding = _make_finding(
            framework_ids={"owasp_mcp_top10": "MCP05"},
        )
        d = finding_to_dict(finding)
        assert d["owasp_id"] == "MCP05"

    def test_finding_to_dict_owasp_id_empty_when_no_framework(self) -> None:
        finding = _make_finding(framework_ids={})
        d = finding_to_dict(finding)
        assert d["owasp_id"] == ""

    def test_finding_to_dict_has_category(self) -> None:
        finding = _make_finding(category="auth")
        d = finding_to_dict(finding)
        assert d["category"] == "auth"

    def test_finding_to_dict_has_framework_ids(self) -> None:
        finding = _make_finding(
            framework_ids={"owasp_mcp_top10": "MCP05", "cwe": ["CWE-78"]},
        )
        d = finding_to_dict(finding)
        assert d["framework_ids"]["owasp_mcp_top10"] == "MCP05"
        assert d["framework_ids"]["cwe"] == ["CWE-78"]

    def test_finding_to_dict_includes_mitigation(self) -> None:
        """Verify mitigation is serialized in finding dict."""
        from q_ai.core.mitigation import (
            GuidanceSection,
            MitigationGuidance,
            SectionKind,
            SourceType,
        )

        guidance = MitigationGuidance(
            sections=[
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=["owasp_mcp_top10"],
                    items=["Validate inputs"],
                ),
            ],
        )
        finding = _make_finding(mitigation=guidance)
        d = finding_to_dict(finding)

        assert "mitigation" in d
        assert d["mitigation"]["sections"][0]["kind"] == "actions"
        assert d["mitigation"]["schema_version"] == 1

    def test_finding_to_dict_null_mitigation(self) -> None:
        """Verify None mitigation serializes as null."""
        finding = _make_finding(mitigation=None)
        d = finding_to_dict(finding)
        assert d["mitigation"] is None

    def test_roundtrip_with_mitigation(self) -> None:
        """Verify finding roundtrip preserves mitigation data."""
        from q_ai.core.mitigation import (
            GuidanceSection,
            MitigationGuidance,
            SectionKind,
            SourceType,
        )

        guidance = MitigationGuidance(
            sections=[
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=["owasp_mcp_top10"],
                    items=["Validate inputs"],
                ),
            ],
            caveats=["Test caveat"],
        )
        original = _make_finding(mitigation=guidance)
        d = finding_to_dict(original)
        restored = dict_to_finding(d)

        assert restored.mitigation is not None
        assert restored.mitigation.sections[0].items == ["Validate inputs"]
        assert restored.mitigation.caveats == ["Test caveat"]


class TestJsonReport:
    def test_generate_json_report(self) -> None:
        """Verify JSON report structure and content."""
        finding = _make_finding()
        scan_result = FakeScanResult(
            findings=[finding],
            server_info={"name": "test-server"},
            tools_scanned=3,
            scanners_run=["injection"],
            finished_at=datetime.now(UTC),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = generate_json_report(scan_result, Path(tmp_dir) / "report.json")
            assert output.exists()

            data = json.loads(output.read_text())
            assert "version" in data
            assert "counteragent_version" not in data
            assert data["summary"]["total_findings"] == 1
            assert len(data["findings"]) == 1
            assert data["findings"][0]["owasp_id"] == "MCP05"
            assert data["findings"][0]["category"] == "command_injection"
            assert "prompt" in data

    def test_generate_json_report_no_findings(self) -> None:
        scan_result = FakeScanResult(
            server_info={"name": "empty-server"},
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = generate_json_report(scan_result, Path(tmp_dir) / "report.json")
            data = json.loads(output.read_text())
            assert data["summary"]["total_findings"] == 0
            assert data["findings"] == []


class TestSarifReport:
    def test_generate_sarif_report(self) -> None:
        """Verify SARIF report structure and content."""
        finding = _make_finding()
        scan_result = FakeScanResult(
            findings=[finding],
            server_info={"name": "test-server"},
            tools_scanned=3,
            scanners_run=["injection"],
            finished_at=datetime.now(UTC),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = generate_sarif_report(scan_result, Path(tmp_dir) / "report.sarif")
            assert output.exists()

            data = json.loads(output.read_text())
            assert data["version"] == "2.1.0"
            assert data["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"

            run = data["runs"][0]
            driver = run["tool"]["driver"]
            assert driver["name"] == "q-ai"
            assert "counteragent" not in driver["name"]
            assert len(driver["rules"]) == 1
            assert driver["rules"][0]["id"] == "MCP05-001"
            assert "command_injection" in driver["rules"][0]["properties"]["tags"]

            assert len(run["results"]) == 1
            assert run["results"][0]["ruleId"] == "MCP05-001"

    def test_sarif_deduplicates_rules(self) -> None:
        """Verify that duplicate rule_ids produce one rule entry."""
        f1 = _make_finding(rule_id="MCP05-001")
        f2 = _make_finding(rule_id="MCP05-001", tool_name="other_tool")
        scan_result = FakeScanResult(
            findings=[f1, f2],
            server_info={"name": "test-server"},
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = generate_sarif_report(scan_result, Path(tmp_dir) / "report.sarif")
            data = json.loads(output.read_text())

            rules = data["runs"][0]["tool"]["driver"]["rules"]
            assert len(rules) == 1
            results = data["runs"][0]["results"]
            assert len(results) == 2


class TestSeverityFromCvss:
    def test_critical(self) -> None:
        assert severity_from_cvss(9.0) == Severity.CRITICAL
        assert severity_from_cvss(10.0) == Severity.CRITICAL

    def test_high(self) -> None:
        assert severity_from_cvss(7.0) == Severity.HIGH
        assert severity_from_cvss(8.9) == Severity.HIGH

    def test_medium(self) -> None:
        assert severity_from_cvss(4.0) == Severity.MEDIUM
        assert severity_from_cvss(6.9) == Severity.MEDIUM

    def test_low(self) -> None:
        assert severity_from_cvss(0.1) == Severity.LOW
        assert severity_from_cvss(3.9) == Severity.LOW

    def test_info(self) -> None:
        assert severity_from_cvss(0.0) == Severity.INFO

    def test_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="CVSS score must be between"):
            severity_from_cvss(-1.0)
        with pytest.raises(ValueError, match="CVSS score must be between"):
            severity_from_cvss(10.1)
