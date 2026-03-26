"""Tests for PyRIT JSON parser."""

from pathlib import Path

import pytest

from q_ai.core.models import Severity
from q_ai.imports.pyrit import parse_pyrit

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_pyrit_happy_path() -> None:
    result = parse_pyrit(FIXTURES / "pyrit_conversations.json")

    assert result.tool_name == "pyrit"
    assert result.tool_version is None
    # 4 conversations, but conv-004 has no scoring → 3 findings.
    assert len(result.findings) == 3
    assert result.errors == []

    # conv-001: true_false, "false" → INFO
    f0 = result.findings[0]
    assert f0.severity == Severity.INFO
    assert f0.category == "prompt_injection"

    # conv-002: true_false, "true" → HIGH
    f1 = result.findings[1]
    assert f1.severity == Severity.HIGH
    assert f1.category == "jailbreak"

    # conv-003: likert, 4 → HIGH
    f2 = result.findings[2]
    assert f2.severity == Severity.HIGH
    assert f2.category == "toxicity_elicitation"


def test_parse_pyrit_unscored_skipped() -> None:
    result = parse_pyrit(FIXTURES / "pyrit_conversations.json")
    ids = [f.original_id for f in result.findings]
    assert "conv-004" not in ids


def test_parse_pyrit_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_pyrit(bad)


def test_parse_pyrit_not_a_list(tmp_path: Path) -> None:
    bad = tmp_path / "obj.json"
    bad.write_text('{"key": "value"}', encoding="utf-8")
    with pytest.raises(TypeError, match="Expected a JSON array"):
        parse_pyrit(bad)


def test_parse_pyrit_missing_fields(tmp_path: Path) -> None:
    """Conversations with partial data should produce warnings, not crashes."""
    data = tmp_path / "partial.json"
    data.write_text(
        '[{"scoring": {"score_type": "unknown_type", "score_value": "x"}}]',
        encoding="utf-8",
    )
    result = parse_pyrit(data)
    assert len(result.findings) == 1
    assert result.findings[0].severity == Severity.MEDIUM
    assert any("Unknown score type" in e for e in result.errors)
