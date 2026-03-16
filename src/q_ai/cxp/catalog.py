"""Rule catalog loader for CXP built-in and user-defined insecure coding rules.

Built-in rules ship with the package under ``q_ai/cxp/rules/*.yaml``.
User rules live in ``~/.qai/cxp/rules/*.yaml`` and override built-in
rules that share the same ID.
"""

from __future__ import annotations

from importlib.resources import files as resource_files
from pathlib import Path

import yaml

from q_ai.cxp.models import Rule

_USER_RULES_DIR = Path.home() / ".qai" / "cxp" / "rules"

_cache: dict[str, list[Rule]] = {}


def _parse_rule(data: dict) -> Rule:
    """Parse a raw YAML dict into a Rule instance.

    Args:
        data: Mapping loaded from a rule YAML file.

    Returns:
        A populated Rule dataclass.
    """
    return Rule(
        id=data["id"],
        name=data["name"],
        category=data["category"],
        severity=data["severity"],
        description=data["description"],
        content=data["content"],
        section=data["section"],
        trigger_prompts=data.get("trigger_prompts", []),
        validators=data.get("validators", []),
    )


def _load_builtin_rules() -> dict[str, Rule]:
    """Load all built-in rules from the package rules directory.

    Returns:
        Mapping of rule ID to Rule, in file-system order.
    """
    rules: dict[str, Rule] = {}
    rules_pkg = resource_files("q_ai.cxp.rules")
    for entry in sorted(rules_pkg.iterdir(), key=lambda e: e.name):
        name = entry.name
        if not name.endswith(".yaml"):
            continue
        raw = entry.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        rule = _parse_rule(data)
        rules[rule.id] = rule
    return rules


def _load_user_rules() -> dict[str, Rule]:
    """Load all user-defined rules from ``~/.qai/cxp/rules/``.

    Returns:
        Mapping of rule ID to Rule (empty if directory does not exist).
    """
    rules: dict[str, Rule] = {}
    if not _USER_RULES_DIR.is_dir():
        return rules
    for path in sorted(_USER_RULES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rule = _parse_rule(data)
        rules[rule.id] = rule
    return rules


def load_catalog() -> list[Rule]:
    """Load all rules from built-in and user directories.

    User rules override built-in rules with the same ID. Results are
    cached after the first call; call ``_invalidate_cache()`` in tests
    to force a reload.

    Returns:
        List of all rules, built-in first then user-added.
    """
    if "catalog" in _cache:
        return _cache["catalog"]
    merged: dict[str, Rule] = _load_builtin_rules()
    merged.update(_load_user_rules())
    _cache["catalog"] = list(merged.values())
    return _cache["catalog"]


def _invalidate_cache() -> None:
    """Invalidate the in-memory catalog cache.

    Intended for test use only — forces the next ``load_catalog()`` call
    to re-read from disk.
    """
    _cache.clear()


def get_rule(rule_id: str) -> Rule | None:
    """Look up a single rule by ID.

    Args:
        rule_id: The rule identifier to look up.

    Returns:
        The matching Rule, or None if not found.
    """
    for rule in load_catalog():
        if rule.id == rule_id:
            return rule
    return None


def list_rules(category: str | None = None) -> list[Rule]:
    """List rules, optionally filtered by category.

    Args:
        category: If provided, return only rules with this category.

    Returns:
        List of matching rules.
    """
    rules = load_catalog()
    if category is None:
        return list(rules)
    return [r for r in rules if r.category == category]


def save_user_rule(rule: Rule) -> Path:
    """Save a rule to the user directory as YAML.

    Creates the user rules directory if it does not exist.
    Invalidates the catalog cache so the new rule is visible on next load.

    Args:
        rule: The rule to persist.

    Returns:
        Path to the written YAML file.
    """
    _USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
    dest = _USER_RULES_DIR / f"{rule.id}.yaml"
    data = {
        "id": rule.id,
        "name": rule.name,
        "category": rule.category,
        "severity": rule.severity,
        "description": rule.description,
        "content": rule.content,
        "section": rule.section,
        "trigger_prompts": rule.trigger_prompts,
        "validators": rule.validators,
    }
    dest.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    _invalidate_cache()
    return dest
