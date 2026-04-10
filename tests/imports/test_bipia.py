"""Tests for BIPIA CSV parser."""

import json
from pathlib import Path

import pytest

from q_ai.core.models import Severity
from q_ai.imports.bipia import parse_bipia

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_parse_bipia_happy_path() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")

    assert result.tool_name == "bipia"
    assert result.tool_version is None
    assert len(result.findings) == 5
    assert result.errors == []


def test_severity_mapping() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")

    # score 1.0 (complied) -> CRITICAL
    assert result.findings[0].severity == Severity.CRITICAL
    # score 0.0 (resisted) -> INFO
    assert result.findings[1].severity == Severity.INFO


def test_taxonomy_bridging() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")

    # instruction_override bridges to prompt_injection (direct)
    f0 = result.findings[0]
    assert f0.category == "prompt_injection"
    assert f0.original_taxonomy == {"ipi_probe": "instruction_override"}

    # context_manipulation bridges to prompt_injection (adjacent)
    f4 = result.findings[4]
    assert f4.category == "prompt_injection"
    assert f4.original_taxonomy == {"ipi_probe": "context_manipulation"}


def test_title_includes_compliance() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")

    assert "complied" in result.findings[0].title
    assert "resisted" in result.findings[1].title
    assert "bipia:" in result.findings[0].title


def test_description_has_prompt_and_response() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")

    desc = result.findings[0].description or ""
    assert "Prompt:" in desc
    assert "Response:" in desc


def test_original_id_from_probe_id_column() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")
    assert result.findings[0].original_id == "bipia-001"
    assert result.findings[2].original_id == "bipia-003"


def test_source_tool() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")
    for f in result.findings:
        assert f.source_tool == "bipia"


def test_raw_evidence_contains_row_data() -> None:
    result = parse_bipia(FIXTURES / "bipia_sample.csv")

    raw = json.loads(result.findings[0].raw_evidence)
    assert raw["category"] == "instruction_override"
    assert raw["complied"] is True
    assert raw["score"] == 1.0


# ---------------------------------------------------------------------------
# Score inference
# ---------------------------------------------------------------------------


def test_score_inferred_from_complied(tmp_path: Path) -> None:
    """When score column is missing, infer from complied."""
    csv_content = "category,prompt,response,complied\n"
    csv_content += "instruction_override,prompt1,resp1,true\n"
    csv_content += "instruction_override,prompt2,resp2,false\n"
    p = tmp_path / "no_score.csv"
    p.write_text(csv_content, encoding="utf-8")

    result = parse_bipia(p)
    assert len(result.findings) == 2
    # complied=true -> score 1.0 -> CRITICAL
    assert result.findings[0].severity == Severity.CRITICAL
    # complied=false -> score 0.0 -> INFO
    assert result.findings[1].severity == Severity.INFO


# ---------------------------------------------------------------------------
# Probe ID fallback
# ---------------------------------------------------------------------------


def test_probe_id_generated_when_missing(tmp_path: Path) -> None:
    csv_content = "category,prompt,response,complied\n"
    csv_content += "task_hijacking,prompt1,resp1,false\n"
    p = tmp_path / "no_id.csv"
    p.write_text(csv_content, encoding="utf-8")

    result = parse_bipia(p)
    assert result.findings[0].original_id == "bipia-0"


# ---------------------------------------------------------------------------
# Unbridged category
# ---------------------------------------------------------------------------


def test_unbridged_category_uses_original(tmp_path: Path) -> None:
    csv_content = "category,prompt,response,complied\n"
    csv_content += "novel_technique,prompt1,resp1,true\n"
    p = tmp_path / "unbridged.csv"
    p.write_text(csv_content, encoding="utf-8")

    result = parse_bipia(p)
    assert result.findings[0].category == "novel_technique"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match=r"empty|no CSV header"):
        parse_bipia(p)


def test_missing_required_columns_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad_cols.csv"
    p.write_text("category,prompt\nfoo,bar\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing required CSV columns"):
        parse_bipia(p)


def test_missing_columns_lists_names(tmp_path: Path) -> None:
    p = tmp_path / "bad_cols2.csv"
    p.write_text("category,prompt\nfoo,bar\n", encoding="utf-8")
    with pytest.raises(ValueError, match="complied"):
        parse_bipia(p)


def test_empty_data_rows(tmp_path: Path) -> None:
    """CSV with header but no data rows produces zero findings."""
    p = tmp_path / "header_only.csv"
    p.write_text("category,prompt,response,complied\n", encoding="utf-8")
    result = parse_bipia(p)
    assert len(result.findings) == 0
    assert result.errors == []


# ---------------------------------------------------------------------------
# Severity thresholds
# ---------------------------------------------------------------------------


def test_severity_boundaries(tmp_path: Path) -> None:
    cases = [
        ("0.0", Severity.INFO),
        ("0.01", Severity.LOW),
        ("0.24", Severity.LOW),
        ("0.25", Severity.MEDIUM),
        ("0.49", Severity.MEDIUM),
        ("0.50", Severity.HIGH),
        ("0.74", Severity.HIGH),
        ("0.75", Severity.CRITICAL),
        ("1.0", Severity.CRITICAL),
    ]
    for score_str, expected in cases:
        csv_content = "category,prompt,response,complied,score\n"
        csv_content += f"instruction_override,p,r,true,{score_str}\n"
        p = tmp_path / f"score_{score_str}.csv"
        p.write_text(csv_content, encoding="utf-8")
        result = parse_bipia(p)
        assert result.findings[0].severity == expected, (
            f"score={score_str}: expected {expected.name}, got {result.findings[0].severity.name}"
        )
