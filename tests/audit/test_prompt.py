"""Tests for the audit report AI-evaluation prompt builder."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from q_ai.audit.reporting.prompt import build_audit_interpret_prompt
from q_ai.mcp.models import ScanFinding, Severity


class _FakeScanResult:
    """Minimal ScanResult stand-in for prompt builder tests."""

    def __init__(
        self,
        findings: list[ScanFinding] | None = None,
    ) -> None:
        self.findings = findings or []
        self.server_info: dict[str, Any] = {
            "name": "test-server",
            "protocolVersion": "2024-11-05",
        }
        self.tools_scanned = 3
        self.scanners_run = ["injection", "auth"]
        self.started_at = datetime(2025, 1, 1, tzinfo=UTC)
        self.finished_at = datetime(2025, 1, 1, 0, 5, tzinfo=UTC)
        self.errors: list[dict[str, str]] = []


def _make_finding(
    rule_id: str = "QAI-INJ-CWE78-test",
    category: str = "command_injection",
    title: str = "Test finding",
    severity: Severity = Severity.HIGH,
) -> ScanFinding:
    return ScanFinding(
        rule_id=rule_id,
        category=category,
        title=title,
        description="A test vulnerability",
        severity=severity,
        evidence="test evidence",
        remediation="fix it",
        tool_name="run_query",
        metadata={},
        timestamp=datetime(2025, 1, 1, 0, 2, tzinfo=UTC),
    )


class TestBuildAuditInterpretPromptWithFindings:
    """Test prompt generation when scan has findings."""

    def test_contains_finding_counts(self) -> None:
        """Prompt includes severity counts and total."""
        findings = [
            _make_finding(severity=Severity.CRITICAL),
            _make_finding(rule_id="QAI-AUTH-001", category="auth", severity=Severity.HIGH),
            _make_finding(rule_id="QAI-PERM-001", category="permissions", severity=Severity.MEDIUM),
        ]
        result = _FakeScanResult(findings)
        prompt = build_audit_interpret_prompt(result)

        assert "3 findings identified" in prompt
        assert "1 critical" in prompt
        assert "1 high" in prompt
        assert "1 medium" in prompt

    def test_contains_owasp_categories(self) -> None:
        """Prompt includes OWASP category IDs when framework_ids are set."""
        f1 = _make_finding(category="command_injection")
        f1.framework_ids = {"owasp_mcp_top10": "MCP05"}
        f2 = _make_finding(rule_id="QAI-AUTH-001", category="auth")
        f2.framework_ids = {"owasp_mcp_top10": "MCP07"}
        findings = [f1, f2]
        result = _FakeScanResult(findings)
        prompt = build_audit_interpret_prompt(result)

        assert "MCP05" in prompt
        assert "MCP07" in prompt
        assert "OWASP categories" in prompt

    def test_contains_target_info(self) -> None:
        """Prompt includes server name and tool count."""
        result = _FakeScanResult([_make_finding()])
        prompt = build_audit_interpret_prompt(result)

        assert "test-server" in prompt
        assert "3 MCP tools" in prompt

    def test_contains_techniques(self) -> None:
        """Prompt includes scanner names as techniques."""
        result = _FakeScanResult([_make_finding()])
        prompt = build_audit_interpret_prompt(result)

        assert "injection" in prompt
        assert "auth" in prompt

    def test_excludes_tool_identity(self) -> None:
        """Prompt must not mention the tool name."""
        result = _FakeScanResult([_make_finding()])
        prompt = build_audit_interpret_prompt(result)

        lower = prompt.lower()
        assert "counteragent" not in lower

    def test_ends_with_action_guidance(self) -> None:
        """Prompt includes prioritization guidance."""
        result = _FakeScanResult([_make_finding()])
        prompt = build_audit_interpret_prompt(result)

        assert "Prioritize findings by exploitability" in prompt


class TestBuildAuditInterpretPromptNoFindings:
    """Test prompt generation when scan has no findings."""

    def test_no_findings_prompt(self) -> None:
        """Prompt indicates no findings and requests coverage review."""
        result = _FakeScanResult([])
        prompt = build_audit_interpret_prompt(result)

        assert "No findings identified" in prompt
        assert "Confirm scan coverage" in prompt

    def test_no_findings_contains_target(self) -> None:
        """Prompt still includes target info with no findings."""
        result = _FakeScanResult([])
        prompt = build_audit_interpret_prompt(result)

        assert "test-server" in prompt
        assert "3 MCP tools" in prompt

    def test_no_findings_excludes_tool_identity(self) -> None:
        """Prompt must not mention the tool name even with no findings."""
        result = _FakeScanResult([])
        prompt = build_audit_interpret_prompt(result)

        lower = prompt.lower()
        assert "counteragent" not in lower
