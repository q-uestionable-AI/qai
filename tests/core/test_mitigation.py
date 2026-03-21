"""Tests for mitigation guidance resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from q_ai.core.mitigation import (
    DEFAULT_DISCLAIMER,
    VALID_CATEGORIES,
    GuidanceSection,
    MitigationGuidance,
    MitigationResolver,
    SectionKind,
    SourceType,
    normalize_metadata,
)
from q_ai.mcp.models import ScanFinding, Severity

# ---------------------------------------------------------------------------
# StrEnum types
# ---------------------------------------------------------------------------


class TestSectionKind:
    """Tests for SectionKind StrEnum values and construction validation."""

    def test_values(self) -> None:
        assert SectionKind.ACTIONS == "actions"
        assert SectionKind.FACTORS == "factors"

    def test_invalid_value_rejected(self) -> None:
        with pytest.raises(ValueError):
            SectionKind("invalid")


class TestSourceType:
    """Tests for SourceType StrEnum values and construction validation."""

    def test_values(self) -> None:
        assert SourceType.TAXONOMY == "taxonomy"
        assert SourceType.RULE == "rule"
        assert SourceType.CONTEXT == "context"

    def test_invalid_value_rejected(self) -> None:
        with pytest.raises(ValueError):
            SourceType("invalid")


# ---------------------------------------------------------------------------
# GuidanceSection
# ---------------------------------------------------------------------------


class TestGuidanceSection:
    """Tests for GuidanceSection serialization and round-trip fidelity."""

    def test_to_dict_round_trip(self) -> None:
        section = GuidanceSection(
            kind=SectionKind.ACTIONS,
            source_type=SourceType.TAXONOMY,
            source_ids=["owasp_mcp_top10", "cwe"],
            items=["Sanitize inputs", "Use allowlists"],
        )
        d = section.to_dict()
        restored = GuidanceSection.from_dict(d)
        assert restored.kind == section.kind
        assert restored.source_type == section.source_type
        assert restored.source_ids == section.source_ids
        assert restored.items == section.items

    def test_to_dict_values(self) -> None:
        section = GuidanceSection(
            kind=SectionKind.FACTORS,
            source_type=SourceType.CONTEXT,
            source_ids=["command_injection"],
            items=["Factor 1"],
        )
        d = section.to_dict()
        assert d["kind"] == "factors"
        assert d["source_type"] == "context"
        assert d["source_ids"] == ["command_injection"]
        assert d["items"] == ["Factor 1"]


# ---------------------------------------------------------------------------
# MitigationGuidance
# ---------------------------------------------------------------------------


class TestMitigationGuidance:
    """Tests for MitigationGuidance defaults, serialization, and fail-soft deserialization."""

    def test_defaults(self) -> None:
        g = MitigationGuidance()
        assert g.sections == []
        assert g.caveats == []
        assert g.schema_version == 1
        assert g.disclaimer == DEFAULT_DISCLAIMER

    def test_to_dict_round_trip(self) -> None:
        section = GuidanceSection(
            kind=SectionKind.ACTIONS,
            source_type=SourceType.TAXONOMY,
            source_ids=["cwe"],
            items=["Action 1"],
        )
        g = MitigationGuidance(
            sections=[section],
            caveats=["A caveat"],
        )
        d = g.to_dict()
        restored = MitigationGuidance.from_dict(d)
        assert len(restored.sections) == 1
        assert restored.sections[0].items == ["Action 1"]
        assert restored.caveats == ["A caveat"]
        assert restored.schema_version == 1
        assert restored.disclaimer == DEFAULT_DISCLAIMER

    def test_from_dict_unknown_schema_version_fail_soft(self) -> None:
        d = {
            "schema_version": 99,
            "sections": [
                {"kind": "actions", "source_type": "taxonomy", "source_ids": [], "items": ["x"]}
            ],
            "caveats": [],
            "disclaimer": "test",
        }
        result = MitigationGuidance.from_dict(d)
        assert result.sections == []
        assert len(result.caveats) == 1
        assert "not supported" in result.caveats[0]

    def test_from_dict_missing_schema_version_defaults_to_1(self) -> None:
        d = {"sections": [], "caveats": ["note"]}
        result = MitigationGuidance.from_dict(d)
        assert result.schema_version == 1
        assert result.caveats == ["note"]


# ---------------------------------------------------------------------------
# Normalization layer
# ---------------------------------------------------------------------------


class TestNormalizeMetadata:
    """Tests for metadata normalization via PREDICATE_MAP and extraction functions."""

    def test_static_mapping(self) -> None:
        predicates = normalize_metadata({"detection_mode": "canary"})
        assert "detection_mode:canary" in predicates

    def test_unknown_keys_ignored(self) -> None:
        predicates = normalize_metadata({"unknown_key_xyz": "value"})
        assert len(predicates) == 0

    def test_compound_write_ratio(self) -> None:
        predicates = normalize_metadata({"write_tools_pct": 0.8})
        assert "permissions:high_write_ratio" in predicates

    def test_compound_write_ratio_below_threshold(self) -> None:
        predicates = normalize_metadata({"write_tools_pct": 0.3})
        assert "permissions:high_write_ratio" not in predicates

    def test_compound_many_tools(self) -> None:
        predicates = normalize_metadata({"tool_count": 25})
        assert "permissions:many_tools" in predicates

    def test_compound_few_tools(self) -> None:
        predicates = normalize_metadata({"tool_count": 5})
        assert "permissions:many_tools" not in predicates

    def test_compound_secret_findings(self) -> None:
        predicates = normalize_metadata({"secret_findings": [{"pattern": "x"}]})
        assert "token:secrets_in_response" in predicates

    def test_compound_env_findings(self) -> None:
        predicates = normalize_metadata({"env_findings": [{"name": "x"}]})
        assert "token:env_var_leak" in predicates

    def test_compound_hidden_chars(self) -> None:
        predicates = normalize_metadata({"hidden_chars": [{"char": "x"}]})
        assert "poisoning:hidden_chars" in predicates

    def test_compound_homoglyphs(self) -> None:
        predicates = normalize_metadata({"homoglyphs": [{"char": "x"}]})
        assert "poisoning:homoglyphs" in predicates

    def test_compound_known_cve(self) -> None:
        predicates = normalize_metadata({"cve_id": "CVE-2025-12345"})
        assert "supply_chain:known_cve" in predicates

    def test_compound_debug_tools(self) -> None:
        predicates = normalize_metadata({"debug_tools": ["tool1"]})
        assert "shadow:debug_tools" in predicates

    def test_compound_cross_tool_ref(self) -> None:
        predicates = normalize_metadata({"referenced_tools": ["tool1"]})
        assert "prompt_injection:cross_tool_ref" in predicates

    def test_multiple_predicates(self) -> None:
        predicates = normalize_metadata(
            {
                "detection_mode": "canary",
                "cwe": "CWE78",
                "technique": "shell_command",
            }
        )
        assert "detection_mode:canary" in predicates
        assert "cwe:command_injection" in predicates
        assert "technique:shell_command" in predicates


# ---------------------------------------------------------------------------
# YAML validation
# ---------------------------------------------------------------------------


class TestYamlValidation:
    """Tests for mitigations.yaml loading, validation, and error detection."""

    def test_bundled_yaml_loads_successfully(self) -> None:
        resolver = MitigationResolver()
        assert resolver is not None

    def test_all_categories_present(self) -> None:
        resolver = MitigationResolver()
        # Resolve for each category — should produce non-empty guidance
        for cat in VALID_CATEGORIES:
            finding = ScanFinding(
                rule_id="test",
                category=cat,
                title="test",
                description="test",
                severity=Severity.MEDIUM,
            )
            guidance = resolver.resolve(finding)
            assert len(guidance.sections) >= 2, f"Category {cat} missing sections"

    def test_raises_on_unknown_category(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(
            "categories:\n"
            "  unknown_cat:\n"
            "    tier1_actions: ['x']\n"
            "    tier3_factors: ['y']\n"
            "rules: {}\n"
        )
        with pytest.raises(ValueError, match="Unknown categories"):
            MitigationResolver(yaml_path=yaml_path)

    def test_raises_on_empty_tier1_actions(self, tmp_path: Path) -> None:
        # Build a valid YAML with one category having empty tier1_actions
        lines = ["categories:\n"]
        for cat in sorted(VALID_CATEGORIES):
            lines.append(f"  {cat}:\n")
            if cat == "auth":
                lines.append("    tier1_actions: []\n")
            else:
                lines.append("    tier1_actions: ['x']\n")
            lines.append("    tier3_factors: ['y']\n")
        lines.append("rules: {}\n")
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text("".join(lines))
        with pytest.raises(ValueError, match="Empty tier1_actions"):
            MitigationResolver(yaml_path=yaml_path)

    def test_raises_on_empty_tier3_factors(self, tmp_path: Path) -> None:
        lines = ["categories:\n"]
        for cat in sorted(VALID_CATEGORIES):
            lines.append(f"  {cat}:\n")
            lines.append("    tier1_actions: ['x']\n")
            if cat == "auth":
                lines.append("    tier3_factors: []\n")
            else:
                lines.append("    tier3_factors: ['y']\n")
        lines.append("rules: {}\n")
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text("".join(lines))
        with pytest.raises(ValueError, match="Empty tier3_factors"):
            MitigationResolver(yaml_path=yaml_path)

    def test_raises_on_empty_rule_actions(self, tmp_path: Path) -> None:
        lines = ["categories:\n"]
        for cat in sorted(VALID_CATEGORIES):
            lines.append(f"  {cat}:\n")
            lines.append("    tier1_actions: ['x']\n")
            lines.append("    tier3_factors: ['y']\n")
        lines.append("rules:\n  'test:predicate':\n    actions: []\n")
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text("".join(lines))
        with pytest.raises(ValueError, match="Empty actions for rule"):
            MitigationResolver(yaml_path=yaml_path)

    def test_raises_on_duplicate_yaml_keys(self, tmp_path: Path) -> None:
        """Duplicate YAML keys are caught at parse time by DuplicateKeyLoader."""
        lines = ["categories:\n"]
        for cat in sorted(VALID_CATEGORIES):
            lines.append(f"  {cat}:\n")
            lines.append("    tier1_actions: ['x']\n")
            lines.append("    tier3_factors: ['y']\n")
        # Duplicate rule key in YAML (not a Python dict — raw YAML text)
        lines.append("rules:\n")
        lines.append("  'dup:pred':\n    actions: ['a']\n")
        lines.append("  'dup:pred':\n    actions: ['b']\n")
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text("".join(lines))
        with pytest.raises(ValueError, match="Duplicate YAML key"):
            MitigationResolver(yaml_path=yaml_path)


# ---------------------------------------------------------------------------
# MitigationResolver
# ---------------------------------------------------------------------------


class TestMitigationResolver:
    """Tests for MitigationResolver section ordering, content, and determinism."""

    def _make_finding(
        self,
        category: str = "command_injection",
        metadata: dict | None = None,
        framework_ids: dict | None = None,
    ) -> ScanFinding:
        return ScanFinding(
            rule_id="TEST-001",
            category=category,
            title="Test finding",
            description="Test description",
            severity=Severity.HIGH,
            metadata=metadata or {},
            framework_ids=framework_ids or {"owasp_mcp_top10": "MCP05", "cwe": ["CWE-78"]},
        )

    def test_resolve_returns_mitigation_guidance(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding()
        result = resolver.resolve(finding)
        assert isinstance(result, MitigationGuidance)

    def test_resolve_has_taxonomy_actions(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding()
        result = resolver.resolve(finding)
        taxonomy_sections = [s for s in result.sections if s.source_type == SourceType.TAXONOMY]
        assert len(taxonomy_sections) == 1
        assert taxonomy_sections[0].kind == SectionKind.ACTIONS
        assert len(taxonomy_sections[0].items) > 0

    def test_resolve_has_factors(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding()
        result = resolver.resolve(finding)
        factor_sections = [s for s in result.sections if s.source_type == SourceType.CONTEXT]
        assert len(factor_sections) == 1
        assert factor_sections[0].kind == SectionKind.FACTORS
        assert len(factor_sections[0].items) > 0

    def test_resolve_with_matching_predicates_has_rule_section(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding(metadata={"detection_mode": "canary", "cwe": "CWE78"})
        result = resolver.resolve(finding)
        rule_sections = [s for s in result.sections if s.source_type == SourceType.RULE]
        assert len(rule_sections) == 1
        assert rule_sections[0].kind == SectionKind.ACTIONS
        assert len(rule_sections[0].items) > 0

    def test_resolve_no_rule_section_when_no_predicates_match(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding(metadata={})
        result = resolver.resolve(finding)
        rule_sections = [s for s in result.sections if s.source_type == SourceType.RULE]
        assert len(rule_sections) == 0

    def test_section_ordering_deterministic(self) -> None:
        """Verify: taxonomy first, rule second, factors third."""
        resolver = MitigationResolver()
        finding = self._make_finding(metadata={"detection_mode": "canary"})
        result = resolver.resolve(finding)
        source_types = [s.source_type for s in result.sections]
        assert source_types == [SourceType.TAXONOMY, SourceType.RULE, SourceType.CONTEXT]

    def test_no_empty_sections(self) -> None:
        """Verify no section has zero items."""
        resolver = MitigationResolver()
        for cat in VALID_CATEGORIES:
            finding = self._make_finding(category=cat)
            result = resolver.resolve(finding)
            for section in result.sections:
                assert len(section.items) > 0, (
                    f"Empty section in category {cat}: {section.source_type}"
                )

    def test_schema_version_present(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding()
        result = resolver.resolve(finding)
        assert result.schema_version == 1

    def test_disclaimer_present(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding()
        result = resolver.resolve(finding)
        assert result.disclaimer == DEFAULT_DISCLAIMER

    def test_caveats_from_general_note(self) -> None:
        """prompt_injection has a general_note — verify it populates caveats."""
        resolver = MitigationResolver()
        finding = self._make_finding(category="prompt_injection")
        result = resolver.resolve(finding)
        assert len(result.caveats) > 0
        assert "defense in depth" in result.caveats[0].lower()

    def test_caveats_empty_when_no_general_note(self) -> None:
        """command_injection has null general_note — no caveats."""
        resolver = MitigationResolver()
        finding = self._make_finding(category="command_injection")
        result = resolver.resolve(finding)
        assert result.caveats == []

    def test_taxonomy_source_ids_are_framework_names(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding(framework_ids={"owasp_mcp_top10": "MCP05", "cwe": ["CWE-78"]})
        result = resolver.resolve(finding)
        taxonomy = next(s for s in result.sections if s.source_type == SourceType.TAXONOMY)
        assert taxonomy.source_ids == ["cwe", "owasp_mcp_top10"]

    def test_factors_source_ids_are_category(self) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding(category="auth")
        result = resolver.resolve(finding)
        factors = next(s for s in result.sections if s.source_type == SourceType.CONTEXT)
        assert factors.source_ids == ["auth"]

    def test_rule_dedup_within_section(self) -> None:
        """If two predicates produce the same action, it should appear only once."""
        resolver = MitigationResolver()
        # Both technique:shell_command and cwe:command_injection produce different actions,
        # but verify no duplicates in general
        finding = self._make_finding(metadata={"detection_mode": "canary"})
        result = resolver.resolve(finding)
        for section in result.sections:
            assert len(section.items) == len(set(section.items)), (
                f"Duplicate items in section {section.source_type}"
            )

    def test_resolve_unknown_category_still_returns_guidance(self) -> None:
        """Unknown category returns guidance with no sections but with defaults."""
        resolver = MitigationResolver()
        finding = self._make_finding(category="nonexistent_category_xyz")
        result = resolver.resolve(finding)
        assert isinstance(result, MitigationGuidance)
        assert result.schema_version == 1
        assert result.disclaimer == DEFAULT_DISCLAIMER

    @pytest.mark.parametrize("category", sorted(VALID_CATEGORIES))
    def test_all_categories_produce_taxonomy_and_factors(self, category: str) -> None:
        resolver = MitigationResolver()
        finding = self._make_finding(category=category)
        result = resolver.resolve(finding)
        source_types = {s.source_type for s in result.sections}
        assert SourceType.TAXONOMY in source_types, f"{category} missing taxonomy"
        assert SourceType.CONTEXT in source_types, f"{category} missing factors"

    def test_to_dict_from_dict_full_round_trip(self) -> None:
        """Full round-trip: resolve → to_dict → from_dict → compare."""
        resolver = MitigationResolver()
        finding = self._make_finding(metadata={"detection_mode": "canary", "cwe": "CWE78"})
        original = resolver.resolve(finding)
        d = original.to_dict()
        restored = MitigationGuidance.from_dict(d)

        assert len(restored.sections) == len(original.sections)
        for orig_s, rest_s in zip(original.sections, restored.sections, strict=True):
            assert orig_s.kind == rest_s.kind
            assert orig_s.source_type == rest_s.source_type
            assert orig_s.source_ids == rest_s.source_ids
            assert orig_s.items == rest_s.items
        assert restored.caveats == original.caveats
        assert restored.schema_version == original.schema_version
        assert restored.disclaimer == original.disclaimer
