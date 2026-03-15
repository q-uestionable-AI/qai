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
