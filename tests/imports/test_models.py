"""Tests for import data models."""

from q_ai.core.models import Severity
from q_ai.imports.models import ImportedFinding, ImportResult, TaxonomyBridge


def test_taxonomy_bridge_construction() -> None:
    bridge = TaxonomyBridge(
        external_framework="owasp_llm_top10",
        external_id="LLM01",
        qai_category="prompt_injection",
        confidence="direct",
    )
    assert bridge.external_framework == "owasp_llm_top10"
    assert bridge.external_id == "LLM01"
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "direct"


def test_imported_finding_defaults() -> None:
    finding = ImportedFinding(
        category="test_cat",
        severity=Severity.HIGH,
        title="Test Finding",
        description=None,
        source_tool="garak",
        source_tool_version=None,
        original_id=None,
    )
    assert finding.original_taxonomy == {}
    assert finding.raw_evidence == ""


def test_import_result_defaults() -> None:
    result = ImportResult(
        findings=[],
        tool_name="garak",
        tool_version=None,
        parser_version="0.1.0",
        source_file="test.jsonl",
    )
    assert result.errors == []
    assert result.findings == []
