"""Tests for base template loading and rule insertion."""

from __future__ import annotations

import pytest

from q_ai.cxp.base_loader import (
    _FORMAT_FILES,
    insert_rules,
    load_base,
    strip_markers,
)
from q_ai.cxp.models import Rule


def _make_rule(
    rule_id: str = "test-rule",
    section: str = "dependencies",
    markdown: str = "- Use test-lib for testing.",
    plaintext: str = "Use test-lib for testing.",
) -> Rule:
    """Create a minimal Rule for testing."""
    return Rule(
        id=rule_id,
        name="Test Rule",
        category="test",
        severity="medium",
        description="A test rule.",
        content={"markdown": markdown, "plaintext": plaintext},
        section=section,
        trigger_prompts=["test prompt"],
        validators=["test-validator"],
    )


# --- load_base tests ---


def test_load_base_cursorrules() -> None:
    """Loads cursorrules base, non-empty, contains section markers."""
    content = load_base("cursorrules")
    assert content
    assert "# cxp:section:dependencies" in content
    assert "# cxp:section:error-handling" in content
    assert "# cxp:section:api-routes" in content


def test_load_base_claude_md() -> None:
    """Loads claude-md base, non-empty, contains markdown section markers."""
    content = load_base("claude-md")
    assert content
    assert "<!-- cxp:section:dependencies -->" in content
    assert "<!-- cxp:section:error-handling -->" in content
    assert "<!-- cxp:section:api-routes -->" in content


def test_load_base_all_formats() -> None:
    """All 6 formats load without error."""
    for format_id in _FORMAT_FILES:
        content = load_base(format_id)
        assert content, f"Empty content for {format_id}"


def test_load_base_unknown_format() -> None:
    """Raises ValueError for unknown format."""
    with pytest.raises(ValueError, match="Unknown format"):
        load_base("nonexistent-format")


# --- insert_rules tests ---


def test_insert_single_rule() -> None:
    """Insert one rule into dependencies section."""
    base = load_base("cursorrules")
    rule = _make_rule(section="dependencies")
    result = insert_rules(base, [rule], syntax="plaintext")
    assert "Use test-lib for testing." in result


def test_insert_multiple_rules_same_section() -> None:
    """Two rules in dependencies, both appear."""
    base = load_base("cursorrules")
    rule1 = _make_rule(rule_id="rule-1", section="dependencies", plaintext="Rule one content.")
    rule2 = _make_rule(rule_id="rule-2", section="dependencies", plaintext="Rule two content.")
    result = insert_rules(base, [rule1, rule2], syntax="plaintext")
    assert "Rule one content." in result
    assert "Rule two content." in result


def test_insert_rules_different_sections() -> None:
    """Rules in dependencies and api-routes."""
    base = load_base("claude-md")
    rule_dep = _make_rule(rule_id="dep-rule", section="dependencies", markdown="- Dep rule.")
    rule_api = _make_rule(rule_id="api-rule", section="api-routes", markdown="- API rule.")
    result = insert_rules(base, [rule_dep, rule_api], syntax="markdown")
    assert "- Dep rule." in result
    assert "- API rule." in result


def test_insert_preserves_existing_content() -> None:
    """Original section content unchanged after insertion."""
    base = load_base("cursorrules")
    original_line = "Flask for HTTP routing."
    assert original_line in base
    rule = _make_rule(section="dependencies")
    result = insert_rules(base, [rule], syntax="plaintext")
    assert original_line in result


def test_insert_rule_unknown_section() -> None:
    """Rule targeting nonexistent section raises ValueError."""
    base = load_base("cursorrules")
    rule = _make_rule(section="nonexistent-section")
    with pytest.raises(ValueError, match="does not exist"):
        insert_rules(base, [rule], syntax="plaintext")


# --- strip_markers tests ---


def test_strip_markers_markdown() -> None:
    """Removes <!-- cxp:section:* --> markers."""
    content = "line1\n<!-- cxp:section:dependencies -->\nline2\n<!-- cxp:section:api-routes -->\n"
    result = strip_markers(content)
    assert "cxp:section" not in result
    assert "line1" in result
    assert "line2" in result


def test_strip_markers_plaintext() -> None:
    """Removes # cxp:section:* markers."""
    content = "line1\n# cxp:section:dependencies\nline2\n# cxp:section:api-routes\n"
    result = strip_markers(content)
    assert "cxp:section" not in result
    assert "line1" in result
    assert "line2" in result


def test_strip_markers_preserves_content() -> None:
    """All non-marker content unchanged."""
    base = load_base("cursorrules")
    stripped = strip_markers(base)
    # All regular content lines should survive.
    assert "You are an expert in Python, Flask" in stripped
    assert "## Key Principles" in stripped
    assert "## Error Handling" in stripped
    # Markers should be gone.
    assert "cxp:section" not in stripped


# --- Pipeline tests ---


def test_full_pipeline() -> None:
    """Load base -> insert rules -> strip markers -> verify output."""
    base = load_base("cursorrules")
    rule = _make_rule(
        section="dependencies",
        plaintext="Use custom-lib for serialization.",
    )
    assembled = insert_rules(base, [rule], syntax="plaintext")
    final = strip_markers(assembled)
    # Rule content present.
    assert "Use custom-lib for serialization." in final
    # No markers remain.
    assert "cxp:section" not in final


def test_no_cxp_references_in_output() -> None:
    """strip_markers output contains zero 'cxp' references."""
    base = load_base("claude-md")
    rule = _make_rule(section="error-handling", markdown="- Handle errors gracefully.")
    assembled = insert_rules(base, [rule], syntax="markdown")
    final = strip_markers(assembled)
    assert "cxp" not in final.lower()
