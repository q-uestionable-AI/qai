"""Tests for scored-prompts JSON parser."""

import json
from pathlib import Path

import pytest

from q_ai.core.models import Severity
from q_ai.imports.scored import parse_scored

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Per-prompt mode
# ---------------------------------------------------------------------------


def test_parse_per_prompt_happy_path() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")

    assert result.tool_name == "scored-prompts"
    assert result.tool_version == "1.0"
    assert len(result.findings) == 4
    assert result.errors == []


def test_per_prompt_severity_mapping() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")

    # score 1.0 (complied) → CRITICAL
    assert result.findings[0].severity == Severity.CRITICAL
    # score 0.0 (resisted) → INFO
    assert result.findings[1].severity == Severity.INFO


def test_per_prompt_taxonomy_bridging() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")

    # instruction_override bridges to prompt_injection (direct)
    f0 = result.findings[0]
    assert f0.category == "prompt_injection"
    assert f0.original_taxonomy == {"ipi_probe": "instruction_override"}

    # task_hijacking bridges to prompt_injection (direct)
    f2 = result.findings[2]
    assert f2.category == "prompt_injection"
    assert f2.original_taxonomy == {"ipi_probe": "task_hijacking"}


def test_per_prompt_title_includes_compliance() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")

    assert "complied" in result.findings[0].title
    assert "resisted" in result.findings[1].title


def test_per_prompt_description_has_prompt_and_response() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")

    f0 = result.findings[0]
    assert "Prompt:" in (f0.description or "")
    assert "Response:" in (f0.description or "")


def test_per_prompt_original_id() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")
    assert result.findings[0].original_id == "probe-001"
    assert result.findings[1].original_id == "probe-002"


def test_per_prompt_source_tool() -> None:
    result = parse_scored(FIXTURES / "scored_per_prompt.json")
    for f in result.findings:
        assert f.source_tool == "scored-prompts"


# ---------------------------------------------------------------------------
# Aggregate mode
# ---------------------------------------------------------------------------


def test_parse_aggregate_happy_path() -> None:
    result = parse_scored(FIXTURES / "scored_aggregate.json")

    assert result.tool_name == "bipia"
    assert result.tool_version == "2025.1"
    assert len(result.findings) == 1
    assert result.errors == []


def test_aggregate_severity() -> None:
    result = parse_scored(FIXTURES / "scored_aggregate.json")
    # overall_compliance_rate = 0.3 → >= 0.25 → MEDIUM
    assert result.findings[0].severity == Severity.MEDIUM


def test_aggregate_title_includes_probe_count() -> None:
    result = parse_scored(FIXTURES / "scored_aggregate.json")
    assert "100 probes" in result.findings[0].title
    assert "30%" in result.findings[0].title


def test_aggregate_description_has_category_breakdown() -> None:
    result = parse_scored(FIXTURES / "scored_aggregate.json")
    desc = result.findings[0].description or ""
    assert "instruction_override" in desc
    assert "task_hijacking" in desc


def test_aggregate_uses_source_field() -> None:
    """When ``source`` field is present, use it instead of ``format``."""
    result = parse_scored(FIXTURES / "scored_aggregate.json")
    assert result.tool_name == "bipia"
    assert result.findings[0].source_tool == "bipia"


# ---------------------------------------------------------------------------
# Severity threshold boundaries
# ---------------------------------------------------------------------------


def test_severity_boundaries(tmp_path: Path) -> None:
    """Verify all severity threshold boundaries."""
    cases = [
        (0.0, Severity.INFO),
        (0.01, Severity.LOW),
        (0.24, Severity.LOW),
        (0.25, Severity.MEDIUM),
        (0.49, Severity.MEDIUM),
        (0.50, Severity.HIGH),
        (0.74, Severity.HIGH),
        (0.75, Severity.CRITICAL),
        (1.0, Severity.CRITICAL),
    ]
    for score, expected_severity in cases:
        data = {
            "format": "scored-prompts",
            "model": "test-model",
            "results": [
                {
                    "probe_id": "p1",
                    "category": "instruction_override",
                    "complied": score > 0,
                    "score": score,
                    "response_text": "resp",
                    "user_prompt": "prompt",
                }
            ],
        }
        p = tmp_path / f"score_{score}.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        result = parse_scored(p)
        assert result.findings[0].severity == expected_severity, (
            f"score={score}: expected {expected_severity.name}, "
            f"got {result.findings[0].severity.name}"
        )


# ---------------------------------------------------------------------------
# Unknown fields pass-through
# ---------------------------------------------------------------------------


def test_unknown_fields_preserved(tmp_path: Path) -> None:
    """Unknown entry fields are preserved in raw_evidence."""
    data = {
        "format": "scored-prompts",
        "model": "test-model",
        "results": [
            {
                "probe_id": "p1",
                "category": "instruction_override",
                "complied": True,
                "score": 1.0,
                "response_text": "resp",
                "user_prompt": "prompt",
                "custom_field": "custom_value",
                "benchmark_version": "2.0",
            }
        ],
    }
    p = tmp_path / "extra.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = parse_scored(p)

    raw = json.loads(result.findings[0].raw_evidence)
    assert raw["custom_field"] == "custom_value"
    assert raw["benchmark_version"] == "2.0"
    assert "_extra_fields" in raw


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        parse_scored(p)


def test_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_scored(p)


def test_not_object_raises(tmp_path: Path) -> None:
    p = tmp_path / "arr.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(TypeError, match="JSON object"):
        parse_scored(p)


def test_missing_model_raises(tmp_path: Path) -> None:
    p = tmp_path / "no_model.json"
    p.write_text('{"format": "scored-prompts"}', encoding="utf-8")
    with pytest.raises(ValueError, match="model"):
        parse_scored(p)


def test_empty_results_imports_zero_findings(tmp_path: Path) -> None:
    """Empty results array triggers aggregate mode with one finding."""
    data = {
        "format": "scored-prompts",
        "model": "test-model",
        "results": [],
        "total_probes": 0,
        "overall_compliance_rate": 0.0,
    }
    p = tmp_path / "empty_results.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = parse_scored(p)
    # Falls through to aggregate mode since results is empty.
    assert len(result.findings) == 1
    assert result.errors == []


def test_malformed_entry_skipped_with_warning(tmp_path: Path) -> None:
    """Non-dict entries in results are skipped with a warning."""
    data = {
        "format": "scored-prompts",
        "model": "test-model",
        "results": [
            "not a dict",
            {
                "probe_id": "p1",
                "category": "instruction_override",
                "complied": False,
                "score": 0.0,
                "response_text": "resp",
                "user_prompt": "prompt",
            },
        ],
    }
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = parse_scored(p)
    assert len(result.findings) == 1
    assert len(result.errors) == 1
    assert "Entry 0" in result.errors[0]


# ---------------------------------------------------------------------------
# Unbridged category fallback
# ---------------------------------------------------------------------------


def test_unbridged_category_uses_original(tmp_path: Path) -> None:
    """Categories not in the bridge table fall back to the original name."""
    data = {
        "format": "scored-prompts",
        "model": "test-model",
        "results": [
            {
                "probe_id": "p1",
                "category": "novel_attack_vector",
                "complied": True,
                "score": 1.0,
                "response_text": "resp",
                "user_prompt": "prompt",
            }
        ],
    }
    p = tmp_path / "unbridged.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = parse_scored(p)
    assert result.findings[0].category == "novel_attack_vector"
