"""Tests for HTML report generation.

Validates that generate_html_report produces a self-contained HTML file
with correct structure, severity badges, HTML escaping, and report sections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from q_ai.audit.reporting.html_report import generate_html_report
from q_ai.mcp.models import ScanFinding, Severity

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeScanResult:
    """Minimal ScanResult stand-in for report generation."""

    def __init__(
        self,
        findings: list[ScanFinding] | None = None,
        errors: list[dict[str, str]] | None = None,
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
        self.errors: list[dict[str, str]] = errors or []


def _make_finding(
    rule_id: str = "QAI-INJ-CWE78-test",
    category: str = "command_injection",
    title: str = "Test finding",
    description: str = "A test vulnerability",
    severity: Severity = Severity.HIGH,
    tool_name: str = "run_query",
    evidence: str = "test evidence",
) -> ScanFinding:
    return ScanFinding(
        rule_id=rule_id,
        category=category,
        title=title,
        description=description,
        severity=severity,
        evidence=evidence,
        remediation="fix it",
        tool_name=tool_name,
        metadata={"payload": "test"},
        timestamp=datetime(2025, 1, 1, 0, 2, tzinfo=UTC),
    )


@pytest.fixture()
def sample_findings() -> list[ScanFinding]:
    return [
        _make_finding(
            rule_id="QAI-INJ-CWE78-test",
            category="command_injection",
            title="Command injection",
            severity=Severity.CRITICAL,
        ),
        _make_finding(
            rule_id="QAI-AUTH-001",
            category="auth",
            title="Missing auth",
            severity=Severity.HIGH,
        ),
        _make_finding(
            rule_id="QAI-PERM-001",
            category="permissions",
            title="Privilege escalation",
            severity=Severity.MEDIUM,
        ),
        _make_finding(
            rule_id="QAI-TOK-001",
            category="token_exposure",
            title="Token exposure",
            severity=Severity.LOW,
        ),
        _make_finding(
            rule_id="QAI-AUDIT-001",
            category="audit_telemetry",
            title="Missing telemetry",
            severity=Severity.INFO,
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHtmlReportCreatesFile:
    """Test that generate_html_report creates a valid HTML file."""

    def test_generate_html_report_creates_file(
        self,
        sample_findings: list[ScanFinding],
        tmp_path: Path,
    ) -> None:
        """Generates report from ScanResult with findings, file exists with HTML structure."""
        result = _FakeScanResult(sample_findings)
        out = tmp_path / "report.html"
        returned_path = generate_html_report(result, out)

        assert returned_path == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "<html" in content
        assert "</html>" in content
        assert "q-ai Scan Report" in content
        assert "test-server" in content

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Output path parent directories are created automatically."""
        result = _FakeScanResult([])
        out = tmp_path / "deep" / "nested" / "report.html"
        generate_html_report(result, out)
        assert out.exists()


class TestHtmlReportContainsAllFindings:
    """Test that every finding's title and rule_id appear in the output."""

    def test_html_report_contains_all_findings(
        self,
        sample_findings: list[ScanFinding],
        tmp_path: Path,
    ) -> None:
        """Each finding's title and rule_id appear in the output."""
        result = _FakeScanResult(sample_findings)
        out = tmp_path / "report.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        for finding in sample_findings:
            assert finding.title in content
            assert finding.rule_id in content
            assert finding.category in content


class TestHtmlReportSeverityBadges:
    """Test that severity values map to correct CSS classes/colors."""

    def test_html_report_severity_badges(
        self,
        sample_findings: list[ScanFinding],
        tmp_path: Path,
    ) -> None:
        """Severity values map to correct CSS classes and colors."""
        result = _FakeScanResult(sample_findings)
        out = tmp_path / "report.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        expected_colors = {
            "critical": "#ef4444",
            "high": "#f97316",
            "medium": "#eab308",
            "low": "#3b82f6",
            "info": "#6b7280",
        }
        for sev, color in expected_colors.items():
            assert f"severity-{sev}" in content
            assert color in content


class TestHtmlReportEmptyFindings:
    """Test ScanResult with zero findings produces valid HTML."""

    def test_html_report_empty_findings(self, tmp_path: Path) -> None:
        """ScanResult with zero findings produces valid HTML with no-findings message."""
        result = _FakeScanResult([])
        out = tmp_path / "empty.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        assert "<!DOCTYPE html>" in content
        assert "No findings detected" in content
        assert "0 findings" in content


class TestHtmlReportEscapesHtml:
    """Test that HTML in finding content is properly escaped."""

    def test_html_report_escapes_html(self, tmp_path: Path) -> None:
        """Finding with <script> in description is escaped, not rendered."""
        malicious = _make_finding(
            title="<script>alert('xss')</script>",
            description="Payload: <img onerror=alert(1) src=x>",
            evidence="<script>document.cookie</script>",
        )
        result = _FakeScanResult([malicious])
        out = tmp_path / "escaped.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        # Raw tags must NOT appear
        assert "<script>alert(" not in content
        assert "<img onerror=" not in content

        # Escaped versions must appear
        assert "&lt;script&gt;alert(" in content
        assert "&lt;img onerror=" in content


class TestHtmlReportErrorsSection:
    """Test that ScanResult with errors shows error section."""

    def test_html_report_errors_section(self, tmp_path: Path) -> None:
        """ScanResult with errors shows error section."""
        errors = [
            {"scanner": "injection", "error": "Connection timeout"},
            {"scanner": "auth", "error": "Server refused connection"},
        ]
        result = _FakeScanResult([], errors=errors)
        out = tmp_path / "errors.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        assert "Errors (2)" in content
        assert "injection" in content
        assert "Connection timeout" in content
        assert "auth" in content
        assert "Server refused connection" in content

    def test_no_errors_section_when_empty(self, tmp_path: Path) -> None:
        """No errors section div when there are no errors."""
        result = _FakeScanResult([])
        out = tmp_path / "no_errors.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        # The CSS class exists in <style>, but no actual error div in body
        assert '<div class="errors-section">' not in content


class TestHtmlReportContent:
    """Test report sections are present and well-formed."""

    def test_report_has_all_sections(
        self,
        sample_findings: list[ScanFinding],
        tmp_path: Path,
    ) -> None:
        """Report contains header, summary, metadata, table, cards, footer."""
        result = _FakeScanResult(sample_findings)
        out = tmp_path / "full.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        # Header
        assert "q-ai Scan Report" in content
        assert "test-server" in content
        assert "2024-11-05" in content

        # Summary bar
        assert "5 findings" in content

        # Metadata grid
        assert "injection, auth" in content

        # Findings table
        assert "findings-table" in content

        # Finding detail cards
        assert "finding-card" in content

        # Footer
        assert "q-uestionable-AI/qai" in content

    def test_findings_sorted_by_severity(
        self,
        tmp_path: Path,
    ) -> None:
        """Findings are ordered critical-first in the HTML."""
        findings = [
            _make_finding(title="Low one", severity=Severity.LOW),
            _make_finding(title="Critical one", severity=Severity.CRITICAL),
            _make_finding(title="Medium one", severity=Severity.MEDIUM),
        ]
        result = _FakeScanResult(findings)
        out = tmp_path / "sorted.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        crit_pos = content.index("Critical one")
        med_pos = content.index("Medium one")
        low_pos = content.index("Low one")
        assert crit_pos < med_pos < low_pos

    def test_self_contained_no_external_deps(
        self,
        sample_findings: list[ScanFinding],
        tmp_path: Path,
    ) -> None:
        """Report has no external CSS/JS/font links."""
        result = _FakeScanResult(sample_findings)
        out = tmp_path / "self_contained.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        assert 'rel="stylesheet"' not in content
        assert "<script src=" not in content
        assert "fonts.googleapis" not in content

    def test_print_styles_present(
        self,
        sample_findings: list[ScanFinding],
        tmp_path: Path,
    ) -> None:
        """Report includes @media print styles."""
        result = _FakeScanResult(sample_findings)
        out = tmp_path / "print.html"
        generate_html_report(result, out)
        content = out.read_text(encoding="utf-8")

        assert "@media print" in content
