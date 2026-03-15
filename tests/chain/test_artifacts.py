"""Tests for chain artifact extraction functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from q_ai.chain.artifacts import extract_audit_artifacts, extract_inject_artifacts

# --- Mock objects that duck-type the real interfaces ---


@dataclass
class MockFinding:
    """Duck-typed Finding for testing."""

    severity: str = "medium"
    tool_name: str = ""
    owasp_id: str = ""
    evidence: str = ""


@dataclass
class MockScanResult:
    """Duck-typed ScanResult for testing."""

    findings: list[Any] = field(default_factory=list)


@dataclass
class MockInjectionResult:
    """Duck-typed InjectionResult for testing."""

    payload_name: str = ""
    technique: str = ""
    outcome: str = "clean_refusal"


@dataclass
class MockCampaign:
    """Duck-typed Campaign for testing."""

    results: list[Any] = field(default_factory=list)


class TestExtractAuditArtifacts:
    """Tests for extract_audit_artifacts."""

    def test_findings_present_picks_highest_severity(self) -> None:
        """Extracts artifacts from highest-severity finding."""
        scan_result = MockScanResult(
            findings=[
                MockFinding(
                    severity="low",
                    tool_name="safe_tool",
                    owasp_id="MCP01",
                    evidence="low evidence",
                ),
                MockFinding(
                    severity="critical",
                    tool_name="exec_cmd",
                    owasp_id="MCP05",
                    evidence="critical evidence",
                ),
                MockFinding(
                    severity="high",
                    tool_name="read_file",
                    owasp_id="MCP02",
                    evidence="high evidence",
                ),
            ]
        )
        artifacts = extract_audit_artifacts(scan_result)
        assert artifacts["vulnerable_tool"] == "exec_cmd"
        assert artifacts["vulnerability_type"] == "MCP05"
        assert artifacts["finding_count"] == "3"
        assert artifacts["finding_evidence"] == "critical evidence"

    def test_no_findings_returns_empty_strings(self) -> None:
        """Returns empty strings when no findings present."""
        scan_result = MockScanResult(findings=[])
        artifacts = extract_audit_artifacts(scan_result)
        assert artifacts["vulnerable_tool"] == ""
        assert artifacts["vulnerability_type"] == ""
        assert artifacts["finding_count"] == "0"
        assert artifacts["finding_evidence"] == ""

    def test_multiple_findings_same_severity_picks_first(self) -> None:
        """When multiple findings share highest severity, picks the first."""
        scan_result = MockScanResult(
            findings=[
                MockFinding(
                    severity="high",
                    tool_name="first_tool",
                    owasp_id="MCP01",
                    evidence="first evidence",
                ),
                MockFinding(
                    severity="high",
                    tool_name="second_tool",
                    owasp_id="MCP02",
                    evidence="second evidence",
                ),
            ]
        )
        artifacts = extract_audit_artifacts(scan_result)
        assert artifacts["vulnerable_tool"] == "first_tool"
        assert artifacts["vulnerability_type"] == "MCP01"

    def test_single_finding(self) -> None:
        """Handles single finding correctly."""
        scan_result = MockScanResult(
            findings=[
                MockFinding(
                    severity="medium",
                    tool_name="some_tool",
                    owasp_id="MCP03",
                    evidence="some evidence",
                ),
            ]
        )
        artifacts = extract_audit_artifacts(scan_result)
        assert artifacts["vulnerable_tool"] == "some_tool"
        assert artifacts["finding_count"] == "1"


class TestExtractInjectArtifacts:
    """Tests for extract_inject_artifacts."""

    def test_successful_campaign(self) -> None:
        """Extracts artifacts from a campaign with successful results."""
        campaign = MockCampaign(
            results=[
                MockInjectionResult(
                    payload_name="basic_exfil",
                    technique="description_poisoning",
                    outcome="clean_refusal",
                ),
                MockInjectionResult(
                    payload_name="advanced_exfil",
                    technique="output_injection",
                    outcome="full_compliance",
                ),
                MockInjectionResult(
                    payload_name="subtle_exfil",
                    technique="cross_tool_escalation",
                    outcome="partial_compliance",
                ),
            ]
        )
        artifacts = extract_inject_artifacts(campaign)
        assert artifacts["best_outcome"] == "full_compliance"
        assert artifacts["working_payload"] == "advanced_exfil"
        assert artifacts["working_technique"] == "output_injection"
        assert artifacts["compliance_rate"] == "67"

    def test_all_refusals(self) -> None:
        """Returns empty/zero values when all attempts are refused."""
        campaign = MockCampaign(
            results=[
                MockInjectionResult(outcome="clean_refusal"),
                MockInjectionResult(outcome="clean_refusal"),
            ]
        )
        artifacts = extract_inject_artifacts(campaign)
        assert artifacts["best_outcome"] == ""
        assert artifacts["working_payload"] == ""
        assert artifacts["working_technique"] == ""
        assert artifacts["compliance_rate"] == "0"

    def test_mixed_results(self) -> None:
        """Handles mix of compliance levels correctly."""
        campaign = MockCampaign(
            results=[
                MockInjectionResult(
                    payload_name="p1",
                    technique="t1",
                    outcome="partial_compliance",
                ),
                MockInjectionResult(
                    payload_name="p2",
                    technique="t2",
                    outcome="refusal_with_leak",
                ),
                MockInjectionResult(
                    payload_name="p3",
                    technique="t3",
                    outcome="full_compliance",
                ),
            ]
        )
        artifacts = extract_inject_artifacts(campaign)
        assert artifacts["best_outcome"] == "full_compliance"
        assert artifacts["working_payload"] == "p1"
        assert artifacts["working_technique"] == "t1"
        assert artifacts["compliance_rate"] == "67"

    def test_empty_campaign(self) -> None:
        """Returns defaults for campaign with no results."""
        campaign = MockCampaign(results=[])
        artifacts = extract_inject_artifacts(campaign)
        assert artifacts["best_outcome"] == ""
        assert artifacts["working_payload"] == ""
        assert artifacts["working_technique"] == ""
        assert artifacts["compliance_rate"] == "0"

    def test_only_partial_compliance(self) -> None:
        """Handles campaign with only partial compliance."""
        campaign = MockCampaign(
            results=[
                MockInjectionResult(
                    payload_name="p1",
                    technique="t1",
                    outcome="partial_compliance",
                ),
            ]
        )
        artifacts = extract_inject_artifacts(campaign)
        assert artifacts["best_outcome"] == "partial_compliance"
        assert artifacts["working_payload"] == "p1"
        assert artifacts["compliance_rate"] == "100"
