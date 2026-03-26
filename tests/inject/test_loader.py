"""Tests for the payload template loader module."""

from __future__ import annotations

from pathlib import Path

from q_ai.inject.models import InjectionTechnique, PayloadTemplate
from q_ai.inject.payloads.loader import (
    discover_templates,
    filter_templates,
    load_all_templates,
    load_template,
)


class TestDiscoverTemplates:
    """Template discovery finds YAML files."""

    def test_discovers_default_templates(self) -> None:
        paths = discover_templates()
        assert len(paths) >= 3
        for p in paths:
            assert p.suffix in (".yaml", ".yml")

    def test_discovers_from_custom_dir(self, tmp_path: Path) -> None:
        (tmp_path / "one.yaml").write_text("[]", encoding="utf-8")
        (tmp_path / "two.yml").write_text("[]", encoding="utf-8")
        paths = discover_templates(tmp_path)
        assert len(paths) == 2
        names = {p.name for p in paths}
        assert names == {"one.yaml", "two.yml"}

    def test_empty_dir(self, tmp_path: Path) -> None:
        paths = discover_templates(tmp_path)
        assert paths == []


class TestLoadTemplate:
    """YAML parsing into PayloadTemplate objects."""

    def test_loads_description_poisoning(self) -> None:
        default_dir = Path(__file__).resolve().parent.parent.parent / (
            "src/q_ai/inject/payloads/templates"
        )
        yaml_file = default_dir / "description_poisoning.yaml"
        templates = load_template(yaml_file)
        assert len(templates) == 6

    def test_template_fields(self) -> None:
        default_dir = Path(__file__).resolve().parent.parent.parent / (
            "src/q_ai/inject/payloads/templates"
        )
        yaml_file = default_dir / "description_poisoning.yaml"
        templates = load_template(yaml_file)
        first = templates[0]
        assert first.name
        assert first.technique
        assert first.tool_name
        assert first.tool_description
        assert first.description

    def test_technique_enum_mapping(self) -> None:
        default_dir = Path(__file__).resolve().parent.parent.parent / (
            "src/q_ai/inject/payloads/templates"
        )
        yaml_file = default_dir / "description_poisoning.yaml"
        templates = load_template(yaml_file)
        for t in templates:
            assert isinstance(t.technique, InjectionTechnique)


class TestLoadAllTemplates:
    """Load all templates from default directory."""

    def test_loads_all(self) -> None:
        templates = load_all_templates()
        assert len(templates) >= 13

    def test_all_have_required_fields(self) -> None:
        templates = load_all_templates()
        for t in templates:
            assert t.name, f"Template missing name: {t}"
            assert t.technique, f"Template missing technique: {t}"
            assert t.description, f"Template missing description: {t}"
            assert t.tool_name, f"Template missing tool_name: {t}"
            assert t.tool_description, f"Template missing tool_description: {t}"


class TestFilterTemplates:
    """Template filtering by technique and target agent."""

    def test_filter_by_technique(self) -> None:
        templates = load_all_templates()
        filtered = filter_templates(templates, technique=InjectionTechnique.DESCRIPTION_POISONING)
        assert len(filtered) == 6

    def test_filter_by_output_injection(self) -> None:
        templates = load_all_templates()
        filtered = filter_templates(templates, technique=InjectionTechnique.OUTPUT_INJECTION)
        assert len(filtered) == 4

    def test_filter_by_cross_tool(self) -> None:
        templates = load_all_templates()
        filtered = filter_templates(templates, technique=InjectionTechnique.CROSS_TOOL_ESCALATION)
        assert len(filtered) == 3

    def test_filter_by_target_agent(self) -> None:
        specific = PayloadTemplate(
            name="specific",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            target_agents=["claude"],
            tool_name="t",
            tool_description="d",
        )
        universal = PayloadTemplate(
            name="universal",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            target_agents=[],
            tool_name="t",
            tool_description="d",
        )
        other = PayloadTemplate(
            name="other",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            target_agents=["gpt"],
            tool_name="t",
            tool_description="d",
        )
        result = filter_templates([specific, universal, other], target_agent="claude")
        assert len(result) == 2
        names = {t.name for t in result}
        assert names == {"specific", "universal"}

    def test_filter_no_match(self) -> None:
        templates = load_all_templates()
        # Filter by a technique that exists but combine with a nonexistent agent
        # that no template targets specifically
        filtered = filter_templates(
            templates,
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            target_agent="nonexistent_agent_xyz",
        )
        # All default templates have empty target_agents (universal), so they all match
        # Let's instead create a list with only specific-agent templates
        specific_only = [
            PayloadTemplate(
                name="specific",
                technique=InjectionTechnique.DESCRIPTION_POISONING,
                description="test",
                target_agents=["gpt"],
                tool_name="t",
                tool_description="d",
            )
        ]
        filtered = filter_templates(
            specific_only,
            target_agent="nonexistent_agent_xyz",
        )
        assert filtered == []


class TestFilterTemplatesByCategories:
    """Template filtering by finding categories."""

    def test_filter_by_categories_matching(self) -> None:
        """Templates with overlapping relevant_categories are included."""
        t1 = PayloadTemplate(
            name="t1",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            relevant_categories=["tool_poisoning"],
            tool_name="t",
            tool_description="d",
        )
        t2 = PayloadTemplate(
            name="t2",
            technique=InjectionTechnique.OUTPUT_INJECTION,
            description="test",
            relevant_categories=["prompt_injection"],
            tool_name="t",
            tool_description="d",
        )
        result = filter_templates([t1, t2], categories={"tool_poisoning"})
        assert len(result) == 1
        assert result[0].name == "t1"

    def test_filter_by_categories_universal_included(self) -> None:
        """Templates with empty relevant_categories (universal) are always included."""
        specific = PayloadTemplate(
            name="specific",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            relevant_categories=["tool_poisoning"],
            tool_name="t",
            tool_description="d",
        )
        universal = PayloadTemplate(
            name="universal",
            technique=InjectionTechnique.OUTPUT_INJECTION,
            description="test",
            relevant_categories=[],
            tool_name="t",
            tool_description="d",
        )
        result = filter_templates([specific, universal], categories={"prompt_injection"})
        assert len(result) == 1
        assert result[0].name == "universal"

    def test_filter_by_categories_none_returns_all(self) -> None:
        """When categories is None, all templates returned."""
        t1 = PayloadTemplate(
            name="t1",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            relevant_categories=["tool_poisoning"],
            tool_name="t",
            tool_description="d",
        )
        result = filter_templates([t1], categories=None)
        assert len(result) == 1

    def test_filter_by_categories_multiple_overlap(self) -> None:
        """Template with multiple categories matches if any overlap."""
        t1 = PayloadTemplate(
            name="t1",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            relevant_categories=["tool_poisoning", "prompt_injection"],
            tool_name="t",
            tool_description="d",
        )
        result = filter_templates([t1], categories={"prompt_injection"})
        assert len(result) == 1

    def test_filter_by_categories_no_match(self) -> None:
        """Templates with non-overlapping categories are excluded."""
        t1 = PayloadTemplate(
            name="t1",
            technique=InjectionTechnique.DESCRIPTION_POISONING,
            description="test",
            relevant_categories=["tool_poisoning"],
            tool_name="t",
            tool_description="d",
        )
        result = filter_templates([t1], categories={"auth"})
        assert result == []


class TestRelevantCategoriesLoading:
    """Test that relevant_categories are loaded from YAML templates."""

    def test_builtin_templates_have_relevant_categories(self) -> None:
        """All built-in templates have non-empty relevant_categories."""
        templates = load_all_templates()
        for t in templates:
            assert isinstance(t.relevant_categories, list), f"{t.name} missing relevant_categories"
            assert len(t.relevant_categories) > 0, f"{t.name} has empty relevant_categories"

    def test_relevant_categories_from_yaml(self, tmp_path: Path) -> None:
        """relevant_categories are read from YAML entries."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            "- name: test_payload\n"
            "  technique: description_poisoning\n"
            "  description: test\n"
            "  tool_name: t\n"
            "  tool_description: d\n"
            "  relevant_categories:\n"
            "    - tool_poisoning\n"
            "    - prompt_injection\n",
            encoding="utf-8",
        )
        templates = load_template(yaml_file)
        assert len(templates) == 1
        assert templates[0].relevant_categories == ["tool_poisoning", "prompt_injection"]

    def test_relevant_categories_defaults_empty(self, tmp_path: Path) -> None:
        """Missing relevant_categories defaults to empty list."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            "- name: test_payload\n"
            "  technique: description_poisoning\n"
            "  description: test\n"
            "  tool_name: t\n"
            "  tool_description: d\n",
            encoding="utf-8",
        )
        templates = load_template(yaml_file)
        assert len(templates) == 1
        assert templates[0].relevant_categories == []


class TestMalformedYaml:
    """Graceful handling of malformed YAML files."""

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("{{{not valid yaml", encoding="utf-8")
        result = load_template(yaml_file)
        assert result == []

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("", encoding="utf-8")
        result = load_template(yaml_file)
        assert result == []


class TestMissingFields:
    """Graceful handling of templates missing required fields."""

    def test_missing_name_skipped(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "missing_name.yaml"
        yaml_file.write_text(
            "- technique: description_poisoning\n"
            "  description: test\n"
            "  tool_name: t\n"
            "  tool_description: d\n",
            encoding="utf-8",
        )
        result = load_template(yaml_file)
        assert result == []

    def test_missing_technique_skipped(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "missing_technique.yaml"
        yaml_file.write_text(
            "- name: test\n  description: test\n  tool_name: t\n  tool_description: d\n",
            encoding="utf-8",
        )
        result = load_template(yaml_file)
        assert result == []

    def test_invalid_technique_skipped(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "invalid_technique.yaml"
        yaml_file.write_text(
            "- name: test\n"
            "  technique: nonexistent_value\n"
            "  description: test\n"
            "  tool_name: t\n"
            "  tool_description: d\n",
            encoding="utf-8",
        )
        result = load_template(yaml_file)
        assert result == []
