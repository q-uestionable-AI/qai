"""Prompt reference generator for CXP build output.

Generates a companion markdown file that maps inserted rules to trigger
prompts, with deduplication and coverage-based ranking.
"""

from __future__ import annotations

from collections import defaultdict

from q_ai.cxp.models import Rule


def generate_prompt_reference(rules: list[Rule]) -> str:
    """Generate a markdown prompt reference for the selected rules.

    Content:
    - Lists inserted rules with names
    - Per-rule trigger prompt suggestions
    - Combined prompts that exercise multiple rules (deduplicated, ranked by coverage)
    - Test protocol instructions (open as standalone project, select named model,
      single prompt only, don't answer clarifying questions)

    Args:
        rules: The rules that were inserted.

    Returns:
        Markdown string for prompt-reference.md.
    """
    lines: list[str] = []
    lines.append("# Prompt Reference")
    lines.append("")
    lines.append("## Inserted Rules")
    lines.append("")
    for rule in rules:
        lines.append(f"- **{rule.id}**: {rule.name}")
    lines.append("")

    # Build prompt → rule-IDs mapping for deduplication.
    prompt_to_rules: dict[str, list[str]] = defaultdict(list)
    for rule in rules:
        for prompt in rule.trigger_prompts:
            prompt_to_rules[prompt].append(rule.id)

    # Split into combined (shared) and single-rule prompts.
    combined: list[tuple[str, list[str]]] = []
    single: list[tuple[str, str]] = []
    for prompt, rule_ids in prompt_to_rules.items():
        if len(rule_ids) > 1:
            combined.append((prompt, rule_ids))
        else:
            single.append((prompt, rule_ids[0]))

    # Rank combined prompts by coverage (most rules first).
    combined.sort(key=lambda x: len(x[1]), reverse=True)

    if combined:
        lines.append("## Combined Prompts")
        lines.append("")
        for prompt, rule_ids in combined:
            lines.append(f"- {prompt}")
            lines.append(f"  - Exercises: {', '.join(rule_ids)}")
        lines.append("")

    lines.append("## Per-Rule Prompts")
    lines.append("")
    for rule in rules:
        rule_prompts = [p for p in rule.trigger_prompts if (p, rule.id) in single]
        if rule_prompts:
            lines.append(f"### {rule.id}")
            lines.append("")
            for prompt in rule_prompts:
                lines.append(f"- {prompt}")
            lines.append("")

    lines.append("## Test Protocol")
    lines.append("")
    lines.append("1. Open this directory as a standalone project in the target assistant")
    lines.append("2. Select the named model under test")
    lines.append("3. Use a single prompt from the list above (do not chain prompts)")
    lines.append("4. Do not answer clarifying questions from the assistant")
    lines.append("5. Record the generated output for validation")
    lines.append("")

    return "\n".join(lines)
