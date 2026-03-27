"""Tests for SARIF 2.1.0 parser."""

import json
from pathlib import Path

import pytest

from q_ai.core.models import Severity
from q_ai.imports.sarif import parse_sarif

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sarif_happy_path() -> None:
    result = parse_sarif(FIXTURES / "report.sarif")

    assert result.tool_name == "SecurityScanner"
    assert result.tool_version == "1.2.3"
    assert len(result.findings) == 3
    assert result.errors == []

    # SEC001: security-severity 9.5 → CRITICAL
    f0 = result.findings[0]
    assert f0.severity == Severity.CRITICAL
    assert f0.original_id == "SEC001"
    assert f0.title == "SQL injection in login handler"
    assert f0.description is not None
    assert "unsanitized user input" in f0.description

    # SEC002: security-severity 5.0 → MEDIUM
    f1 = result.findings[1]
    assert f1.severity == Severity.MEDIUM
    assert f1.original_id == "SEC002"

    # SEC003: no security-severity, level=note → LOW
    f2 = result.findings[2]
    assert f2.severity == Severity.LOW
    assert f2.original_id == "SEC003"


def test_parse_sarif_security_severity_takes_precedence() -> None:
    """security-severity property should override SARIF level."""
    result = parse_sarif(FIXTURES / "report.sarif")
    # SEC001 has level=error (normally HIGH) but security-severity=9.5 (CRITICAL).
    assert result.findings[0].severity == Severity.CRITICAL


def test_parse_sarif_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.sarif"
    bad.write_text("{{not json}}", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_sarif(bad)


def test_parse_sarif_wrong_version(tmp_path: Path) -> None:
    bad = tmp_path / "old.sarif"
    bad.write_text(json.dumps({"version": "1.0.0", "runs": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported SARIF version"):
        parse_sarif(bad)


def test_parse_sarif_multiple_runs(tmp_path: Path) -> None:
    """A SARIF file with two runs should produce findings from both."""
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "ToolA", "semanticVersion": "1.0"}},
                "results": [{"ruleId": "A1", "level": "error", "message": {"text": "Finding A1"}}],
            },
            {
                "tool": {"driver": {"name": "ToolB", "semanticVersion": "2.0"}},
                "results": [{"ruleId": "B1", "level": "note", "message": {"text": "Finding B1"}}],
            },
        ],
    }
    path = tmp_path / "multi.sarif"
    path.write_text(json.dumps(sarif), encoding="utf-8")

    result = parse_sarif(path)
    assert len(result.findings) == 2
    # Primary tool info comes from first run.
    assert result.tool_name == "ToolA"
    assert result.tool_version == "1.0"
    assert result.findings[0].original_id == "A1"
    assert result.findings[1].original_id == "B1"
