"""Tests for chain artifact extraction functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from q_ai.chain.artifacts import (
    extract_audit_artifacts,
    extract_cxp_artifacts,
    extract_inject_artifacts,
    extract_ipi_artifacts,
    extract_rxp_artifacts,
)

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


# --- Mock objects for IPI/CXP/RXP ---


@dataclass
class MockIPICampaign:
    """Duck-typed IPI Campaign for testing."""

    technique: str = "white_ink"
    output_path: str = "/tmp/payloads/report.pdf"
    format: str = "pdf"


@dataclass
class MockGenerateResult:
    """Duck-typed GenerateResult for testing."""

    campaigns: list[Any] = field(default_factory=list)
    skipped: int = 0
    errors: list[Any] = field(default_factory=list)


@dataclass
class MockBuildResult:
    """Duck-typed BuildResult for testing."""

    repo_dir: str = ""
    rules_inserted: list[str] = field(default_factory=list)
    format_id: str = ""


@dataclass
class MockValidationResult:
    """Duck-typed ValidationResult for testing."""

    model_id: str = ""
    total_queries: int = 0
    retrieval_rate: float = 0.0
    mean_poison_rank: float | None = None


class TestExtractIPIArtifacts:
    """Tests for extract_ipi_artifacts."""

    def test_campaigns_present(self) -> None:
        """Extracts artifacts from IPI GenerateResult with campaigns."""
        result = MockGenerateResult(
            campaigns=[
                MockIPICampaign(technique="white_ink", output_path="/tmp/out.pdf", format="pdf"),
                MockIPICampaign(technique="font_size", output_path="/tmp/out2.pdf", format="pdf"),
            ]
        )
        artifacts = extract_ipi_artifacts(result)
        assert artifacts["payload_count"] == "2"
        assert artifacts["output_dir"] == "/tmp/out.pdf"
        assert artifacts["format"] == "pdf"
        assert "white_ink" in artifacts["techniques"]
        assert "font_size" in artifacts["techniques"]

    def test_empty_campaigns(self) -> None:
        """Returns defaults for empty campaigns."""
        result = MockGenerateResult(campaigns=[])
        artifacts = extract_ipi_artifacts(result)
        assert artifacts["payload_count"] == "0"
        assert artifacts["output_dir"] == ""
        assert artifacts["format"] == ""
        assert artifacts["techniques"] == ""

    def test_consistent_keys(self) -> None:
        """Always produces the same four keys."""
        result = MockGenerateResult()
        artifacts = extract_ipi_artifacts(result)
        assert set(artifacts.keys()) == {"payload_count", "output_dir", "format", "techniques"}


class TestExtractCXPArtifacts:
    """Tests for extract_cxp_artifacts."""

    def test_build_result_present(self) -> None:
        """Extracts artifacts from CXP BuildResult."""
        result = MockBuildResult(
            repo_dir="/tmp/repo",
            rules_inserted=["rule-1", "rule-2"],
            format_id="cursorrules",
        )
        artifacts = extract_cxp_artifacts(result)
        assert artifacts["repo_dir"] == "/tmp/repo"
        assert artifacts["rules_inserted"] == "rule-1, rule-2"
        assert artifacts["rule_count"] == "2"
        assert artifacts["format_id"] == "cursorrules"

    def test_empty_build_result(self) -> None:
        """Returns defaults for empty BuildResult."""
        result = MockBuildResult()
        artifacts = extract_cxp_artifacts(result)
        assert artifacts["repo_dir"] == ""
        assert artifacts["rule_count"] == "0"
        assert artifacts["rules_inserted"] == ""

    def test_consistent_keys(self) -> None:
        """Always produces the same four keys."""
        result = MockBuildResult()
        artifacts = extract_cxp_artifacts(result)
        assert set(artifacts.keys()) == {"repo_dir", "rules_inserted", "rule_count", "format_id"}


class TestExtractRXPArtifacts:
    """Tests for extract_rxp_artifacts."""

    def test_successful_validation(self) -> None:
        """Extracts artifacts from successful RXP ValidationResult."""
        result = MockValidationResult(
            model_id="minilm-l6",
            total_queries=10,
            retrieval_rate=0.8,
            mean_poison_rank=2.5,
        )
        artifacts = extract_rxp_artifacts(result)
        assert artifacts["retrieval_rate"] == "80%"
        assert artifacts["mean_rank"] == "2.5"
        assert artifacts["model_id"] == "minilm-l6"
        assert artifacts["query_count"] == "10"

    def test_zero_retrieval_rate(self) -> None:
        """Handles zero retrieval rate."""
        result = MockValidationResult(retrieval_rate=0.0, mean_poison_rank=None)
        artifacts = extract_rxp_artifacts(result)
        assert artifacts["retrieval_rate"] == "0%"
        assert artifacts["mean_rank"] == ""

    def test_consistent_keys(self) -> None:
        """Always produces the same four keys."""
        result = MockValidationResult()
        artifacts = extract_rxp_artifacts(result)
        assert set(artifacts.keys()) == {"retrieval_rate", "mean_rank", "model_id", "query_count"}
