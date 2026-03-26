"""Tests for Garak JSONL parser."""

from pathlib import Path

import pytest

from q_ai.core.models import Severity
from q_ai.imports.garak import parse_garak

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_garak_happy_path() -> None:
    result = parse_garak(FIXTURES / "garak_report.jsonl")

    assert result.tool_name == "garak"
    assert result.tool_version == "0.9.0.13"
    assert len(result.findings) == 3
    assert result.errors == []

    # First eval: 2/10 passed = 20% → CRITICAL
    f0 = result.findings[0]
    assert f0.severity == Severity.CRITICAL
    assert "promptinject.HijackHateHumansMini" in f0.title
    assert f0.original_taxonomy.get("owasp_llm") == "LLM01"
    assert f0.original_taxonomy.get("avid") == "AVID-2023-V001"
    # LLM01 bridges to prompt_injection
    assert f0.category == "prompt_injection"

    # Second eval: 8/10 passed = 80% → LOW
    f1 = result.findings[1]
    assert f1.severity == Severity.LOW
    # LLM02 bridges to token_exposure (adjacent)
    assert f1.category == "token_exposure"

    # Third eval: 10/10 passed = 100% → INFO, no taxonomy → probe name as category
    f2 = result.findings[2]
    assert f2.severity == Severity.INFO
    assert f2.category == "leakreplay.LiteraryQuotes"


def test_parse_garak_malformed_no_setup() -> None:
    with pytest.raises(ValueError, match="no 'start_run setup' entry"):
        parse_garak(FIXTURES / "malformed_garak.jsonl")


def test_parse_garak_empty_file() -> None:
    with pytest.raises(ValueError, match="no 'start_run setup' entry"):
        parse_garak(FIXTURES / "empty.jsonl")


def test_parse_garak_attempts_skipped() -> None:
    result = parse_garak(FIXTURES / "garak_report.jsonl")
    # Only eval entries become findings, not attempts.
    assert len(result.findings) == 3
    # Check no attempt data leaked into findings.
    for f in result.findings:
        assert "att-" not in (f.original_id or "")
