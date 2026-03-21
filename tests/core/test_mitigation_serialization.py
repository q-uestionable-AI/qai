"""Golden serialization tests for MitigationGuidance wire format.

These tests protect the compatibility boundary: DB, SARIF, JSON export,
NDJSON, and UI all consume the same structure.
"""

from __future__ import annotations

import json

from q_ai.core.mitigation import (
    DEFAULT_DISCLAIMER,
    GuidanceSection,
    MitigationGuidance,
    SectionKind,
    SourceType,
)


def _make_guidance() -> MitigationGuidance:
    """Build a representative MitigationGuidance for golden tests."""
    return MitigationGuidance(
        sections=[
            GuidanceSection(
                kind=SectionKind.ACTIONS,
                source_type=SourceType.TAXONOMY,
                source_ids=["owasp_mcp_top10"],
                items=["Validate tool input parameters", "Use allowlists for commands"],
            ),
            GuidanceSection(
                kind=SectionKind.ACTIONS,
                source_type=SourceType.RULE,
                source_ids=["detection_mode:canary"],
                items=["Canary confirmed execution — restrict tool access"],
            ),
            GuidanceSection(
                kind=SectionKind.FACTORS,
                source_type=SourceType.CONTEXT,
                source_ids=["command_injection"],
                items=["Severity depends on privilege level"],
            ),
        ],
        caveats=["Server runs with elevated privileges"],
        schema_version=1,
        disclaimer=DEFAULT_DISCLAIMER,
    )


class TestGoldenSerialization:
    """Golden tests for MitigationGuidance wire format contract."""

    def test_to_dict_exact_structure(self) -> None:
        """Known input produces exact expected dict."""
        guidance = _make_guidance()
        d = guidance.to_dict()

        assert d["schema_version"] == 1
        assert d["disclaimer"] == DEFAULT_DISCLAIMER
        assert d["caveats"] == ["Server runs with elevated privileges"]
        assert len(d["sections"]) == 3

        s0 = d["sections"][0]
        assert s0["kind"] == "actions"
        assert s0["source_type"] == "taxonomy"
        assert s0["source_ids"] == ["owasp_mcp_top10"]
        assert s0["items"] == [
            "Validate tool input parameters",
            "Use allowlists for commands",
        ]

        s1 = d["sections"][1]
        assert s1["kind"] == "actions"
        assert s1["source_type"] == "rule"
        assert s1["source_ids"] == ["detection_mode:canary"]

        s2 = d["sections"][2]
        assert s2["kind"] == "factors"
        assert s2["source_type"] == "context"
        assert s2["source_ids"] == ["command_injection"]

    def test_enum_values_are_strings(self) -> None:
        """Enum fields serialize as plain strings, not enum objects."""
        guidance = _make_guidance()
        d = guidance.to_dict()
        raw = json.dumps(d)
        parsed = json.loads(raw)

        for section in parsed["sections"]:
            assert isinstance(section["kind"], str)
            assert isinstance(section["source_type"], str)

    def test_round_trip_fidelity(self) -> None:
        """to_dict → JSON → from_dict produces equal structure."""
        original = _make_guidance()
        serialized = json.dumps(original.to_dict())
        restored = MitigationGuidance.from_dict(json.loads(serialized))

        assert len(restored.sections) == len(original.sections)
        for orig_s, rest_s in zip(original.sections, restored.sections, strict=True):
            assert rest_s.kind == orig_s.kind
            assert rest_s.source_type == orig_s.source_type
            assert rest_s.source_ids == orig_s.source_ids
            assert rest_s.items == orig_s.items
        assert restored.caveats == original.caveats
        assert restored.schema_version == original.schema_version
        assert restored.disclaimer == original.disclaimer

    def test_empty_sections_serialize_as_empty_list(self) -> None:
        """Empty sections produces [] not omitted key."""
        guidance = MitigationGuidance(sections=[], caveats=[])
        d = guidance.to_dict()
        assert d["sections"] == []
        assert d["caveats"] == []
        assert "sections" in d
        assert "caveats" in d

    def test_unknown_schema_version_fail_soft(self) -> None:
        """Unknown schema_version returns fallback, not exception."""
        d = {"schema_version": 999, "sections": [], "caveats": []}
        result = MitigationGuidance.from_dict(d)
        assert result.caveats == ["Mitigation guidance format not supported by this version"]
        assert result.sections == []

    def test_source_ids_namespace_patterns(self) -> None:
        """source_ids follow expected namespace patterns."""
        guidance = _make_guidance()
        d = guidance.to_dict()

        taxonomy_ids = d["sections"][0]["source_ids"]
        assert all("_" in sid or sid.isalpha() for sid in taxonomy_ids)

        rule_ids = d["sections"][1]["source_ids"]
        assert all(":" in sid for sid in rule_ids)
