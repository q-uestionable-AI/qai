"""Tests for the CXP rule catalog loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import q_ai.cxp.catalog as catalog_module
from q_ai.cxp.catalog import (
    _invalidate_cache,
    get_rule,
    list_rules,
    load_catalog,
    save_user_rule,
)
from q_ai.cxp.models import Rule

_BUILTIN_RULE_IDS = {
    "weak-crypto-md5",
    "hardcoded-secrets",
    "no-csrf",
    "shell-true",
    "stack-traces",
    "extra-index-url",
    "insecure-perms",
    "outbound-exfil",
}

_REQUIRED_FIELDS = {
    "id",
    "name",
    "category",
    "severity",
    "description",
    "content",
    "section",
    "trigger_prompts",
    "validators",
}


@pytest.fixture(autouse=True)
def reset_catalog():
    """Invalidate the catalog cache before and after each test."""
    _invalidate_cache()
    yield
    _invalidate_cache()


class TestLoadCatalog:
    def test_load_catalog_returns_builtin_rules(self):
        rules = load_catalog()
        ids = {r.id for r in rules}
        assert _BUILTIN_RULE_IDS.issubset(ids), f"Missing rule IDs: {_BUILTIN_RULE_IDS - ids}"

    def test_load_catalog_rule_count(self):
        rules = load_catalog()
        assert len(rules) >= 8

    def test_load_catalog_rule_schema(self):
        for rule in load_catalog():
            assert isinstance(rule.id, str) and rule.id
            assert isinstance(rule.name, str) and rule.name
            assert isinstance(rule.category, str) and rule.category
            assert rule.severity in {"high", "medium", "low"}
            assert isinstance(rule.description, str) and rule.description
            assert isinstance(rule.content, dict)
            assert isinstance(rule.section, str) and rule.section
            assert isinstance(rule.trigger_prompts, list)
            assert isinstance(rule.validators, list)

    def test_load_catalog_content_keys(self):
        for rule in load_catalog():
            assert "markdown" in rule.content, f"Rule {rule.id!r} missing markdown content"
            assert "plaintext" in rule.content, f"Rule {rule.id!r} missing plaintext content"
            assert rule.content["markdown"].strip()
            assert rule.content["plaintext"].strip()

    def test_load_catalog_is_cached(self):
        first = load_catalog()
        second = load_catalog()
        assert first is second


class TestGetRule:
    def test_get_rule_found(self):
        rule = get_rule("weak-crypto-md5")
        assert rule is not None
        assert rule.id == "weak-crypto-md5"

    def test_get_rule_not_found(self):
        assert get_rule("does-not-exist") is None


class TestListRules:
    def test_list_rules_all(self):
        rules = list_rules()
        assert len(rules) >= 8

    def test_list_rules_by_category(self):
        rules = list_rules(category="weak-crypto")
        assert len(rules) == 1
        assert rules[0].id == "weak-crypto-md5"

    def test_list_rules_unknown_category(self):
        assert list_rules(category="nonexistent-category") == []

    def test_list_rules_returns_copy(self):
        a = list_rules()
        b = list_rules()
        assert a is not b


class TestUserRules:
    def test_user_rules_override_builtin(self, tmp_path, monkeypatch):
        user_rules_dir = tmp_path / "cxp" / "rules"
        user_rules_dir.mkdir(parents=True)
        monkeypatch.setattr(catalog_module, "_USER_RULES_DIR", user_rules_dir)
        _invalidate_cache()

        override_data = {
            "id": "weak-crypto-md5",
            "name": "Custom Override",
            "category": "weak-crypto",
            "severity": "low",
            "description": "Overridden for testing.",
            "content": {"markdown": "override md", "plaintext": "override txt"},
            "section": "dependencies",
            "trigger_prompts": ["test prompt"],
            "validators": [],
        }
        (user_rules_dir / "weak-crypto-md5.yaml").write_text(
            yaml.dump(override_data), encoding="utf-8"
        )

        rules = load_catalog()
        rule = next(r for r in rules if r.id == "weak-crypto-md5")
        assert rule.name == "Custom Override"
        assert rule.severity == "low"

    def test_user_rules_override_does_not_duplicate(self, tmp_path, monkeypatch):
        user_rules_dir = tmp_path / "cxp" / "rules"
        user_rules_dir.mkdir(parents=True)
        monkeypatch.setattr(catalog_module, "_USER_RULES_DIR", user_rules_dir)
        _invalidate_cache()

        override_data = {
            "id": "weak-crypto-md5",
            "name": "Custom Override",
            "category": "weak-crypto",
            "severity": "low",
            "description": "Overridden.",
            "content": {"markdown": "md", "plaintext": "txt"},
            "section": "dependencies",
            "trigger_prompts": [],
            "validators": [],
        }
        (user_rules_dir / "weak-crypto-md5.yaml").write_text(
            yaml.dump(override_data), encoding="utf-8"
        )

        rules = load_catalog()
        ids = [r.id for r in rules]
        assert ids.count("weak-crypto-md5") == 1

    def test_save_user_rule(self, tmp_path, monkeypatch):
        user_rules_dir = tmp_path / "cxp" / "rules"
        monkeypatch.setattr(catalog_module, "_USER_RULES_DIR", user_rules_dir)
        _invalidate_cache()

        new_rule = Rule(
            id="test-save-rule",
            name="Test Save Rule",
            category="test-category",
            severity="medium",
            description="A rule created during testing.",
            content={"markdown": "## test md", "plaintext": "test txt"},
            section="api-routes",
            trigger_prompts=["write a test endpoint"],
            validators=["test-validator"],
        )
        path = save_user_rule(new_rule)
        assert path.exists()
        assert path.suffix == ".yaml"

        found = get_rule("test-save-rule")
        assert found is not None
        assert found.name == "Test Save Rule"

    def test_save_user_rule_creates_directory(self, tmp_path, monkeypatch):
        user_rules_dir = tmp_path / "deep" / "nested" / "dir"
        monkeypatch.setattr(catalog_module, "_USER_RULES_DIR", user_rules_dir)
        _invalidate_cache()

        rule = Rule(
            id="dir-create-rule",
            name="Dir Create Rule",
            category="test-category",
            severity="low",
            description="Tests directory creation.",
            content={"markdown": "md", "plaintext": "txt"},
            section="dependencies",
            trigger_prompts=[],
            validators=[],
        )
        save_user_rule(rule)
        assert user_rules_dir.is_dir()


class TestYamlRoundtrip:
    def test_rule_yaml_roundtrip(self, tmp_path, monkeypatch):
        user_rules_dir = tmp_path / "cxp" / "rules"
        monkeypatch.setattr(catalog_module, "_USER_RULES_DIR", user_rules_dir)
        _invalidate_cache()

        original = get_rule("hardcoded-secrets")
        assert original is not None

        path = save_user_rule(original)
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

        assert raw["id"] == original.id
        assert raw["name"] == original.name
        assert raw["category"] == original.category
        assert raw["severity"] == original.severity
        assert raw["description"].strip() == original.description.strip()
        assert raw["content"]["markdown"].strip() == original.content["markdown"].strip()
        assert raw["content"]["plaintext"].strip() == original.content["plaintext"].strip()
        assert raw["section"] == original.section
        assert raw["trigger_prompts"] == original.trigger_prompts
        assert raw["validators"] == original.validators
