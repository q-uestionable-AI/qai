"""Tests for prompt reference generation."""

from __future__ import annotations

from q_ai.cxp.models import Rule
from q_ai.cxp.prompt_reference import generate_prompt_reference


def _make_rule(
    rule_id: str,
    name: str = "Test Rule",
    trigger_prompts: list[str] | None = None,
) -> Rule:
    """Create a minimal Rule for testing."""
    return Rule(
        id=rule_id,
        name=name,
        category="test",
        severity="high",
        description="Test rule.",
        content={"markdown": "test content", "plaintext": "test content"},
        section="dependencies",
        trigger_prompts=trigger_prompts or [],
        validators=[],
    )


class TestGeneratePromptReferenceSingleRule:
    def test_generate_prompt_reference_single_rule(self) -> None:
        rule = _make_rule("rule-a", "Rule Alpha", ["Prompt one", "Prompt two"])
        output = generate_prompt_reference([rule])
        assert "# Prompt Reference" in output
        assert "rule-a" in output
        assert "Rule Alpha" in output
        assert "Prompt one" in output
        assert "Prompt two" in output
        assert "## Test Protocol" in output


class TestGeneratePromptReferenceMultipleRules:
    def test_generate_prompt_reference_multiple_rules(self) -> None:
        rules = [
            _make_rule("rule-a", "Rule Alpha", ["Prompt A1"]),
            _make_rule("rule-b", "Rule Beta", ["Prompt B1"]),
        ]
        output = generate_prompt_reference(rules)
        assert "rule-a" in output
        assert "rule-b" in output
        assert "Prompt A1" in output
        assert "Prompt B1" in output


class TestPromptDeduplication:
    def test_prompt_deduplication(self) -> None:
        """Shared prompts listed once under Combined."""
        shared_prompt = "Build the authentication module"
        rules = [
            _make_rule("rule-a", "Rule Alpha", [shared_prompt, "Unique A"]),
            _make_rule("rule-b", "Rule Beta", [shared_prompt, "Unique B"]),
        ]
        output = generate_prompt_reference(rules)
        # The shared prompt should appear under Combined
        assert "## Combined Prompts" in output
        combined_section = output.split("## Combined Prompts")[1].split("## Per-Rule Prompts")[0]
        assert shared_prompt in combined_section
        assert "rule-a" in combined_section
        assert "rule-b" in combined_section
        # Shared prompt should NOT appear in per-rule sections
        per_rule_section = output.split("## Per-Rule Prompts")[1].split("## Test Protocol")[0]
        assert shared_prompt not in per_rule_section
        # Unique prompts should still appear in per-rule
        assert "Unique A" in per_rule_section
        assert "Unique B" in per_rule_section


class TestPromptRanking:
    def test_prompt_ranking(self) -> None:
        """Combined prompts ranked before single-rule prompts."""
        shared_prompt = "Shared prompt"
        rules = [
            _make_rule("rule-a", "Rule Alpha", [shared_prompt, "Unique A"]),
            _make_rule("rule-b", "Rule Beta", [shared_prompt, "Unique B"]),
        ]
        output = generate_prompt_reference(rules)
        # Combined section should come before Per-Rule section
        combined_idx = output.index("## Combined Prompts")
        per_rule_idx = output.index("## Per-Rule Prompts")
        assert combined_idx < per_rule_idx

    def test_combined_prompts_ranked_by_coverage(self) -> None:
        """Combined prompts covering more rules rank first."""
        prompt_3 = "Covers three rules"
        prompt_2 = "Covers two rules"
        rules = [
            _make_rule("rule-a", "A", [prompt_3, prompt_2]),
            _make_rule("rule-b", "B", [prompt_3, prompt_2]),
            _make_rule("rule-c", "C", [prompt_3]),
        ]
        output = generate_prompt_reference(rules)
        combined_section = output.split("## Combined Prompts")[1].split("## Per-Rule Prompts")[0]
        idx_3 = combined_section.index(prompt_3)
        idx_2 = combined_section.index(prompt_2)
        assert idx_3 < idx_2
