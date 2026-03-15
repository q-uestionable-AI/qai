"""Base template loading and rule insertion for CXP context files.

Provides utilities for loading format-specific base templates, inserting
rule content at section markers, and stripping markers from final output.
"""

from __future__ import annotations

import re
from importlib.resources import files as resource_files

from q_ai.cxp.models import Rule

# Maps format_id → base template filename.
_FORMAT_FILES: dict[str, str] = {
    "cursorrules": "cursorrules.txt",
    "claude-md": "claude-md.md",
    "copilot-instructions": "copilot-instructions.md",
    "agents-md": "agents-md.md",
    "gemini-md": "gemini-md.md",
    "windsurfrules": "windsurfrules.txt",
}

# Regex patterns for section markers.
_MARKDOWN_MARKER = re.compile(r"^<!-- cxp:section:(\S+) -->$", re.MULTILINE)
_PLAINTEXT_MARKER = re.compile(r"^# cxp:section:(\S+)$", re.MULTILINE)

# Pattern matching any marker (either style) for stripping.
_ANY_MARKER = re.compile(r"^(?:<!-- cxp:section:\S+ -->|# cxp:section:\S+)\n?", re.MULTILINE)


def load_base(format_id: str) -> str:
    """Load the base template for a format.

    Args:
        format_id: Format identifier (e.g., "cursorrules", "claude-md").

    Returns:
        Raw template text including section markers.

    Raises:
        ValueError: If format_id is not recognized.
    """
    filename = _FORMAT_FILES.get(format_id)
    if filename is None:
        raise ValueError(
            f"Unknown format: {format_id!r}. Valid formats: {', '.join(sorted(_FORMAT_FILES))}"
        )
    bases = resource_files("q_ai.cxp.bases")
    return (bases / filename).read_text(encoding="utf-8")


def _find_section_end(lines: list[str], marker_line: int, marker_re: re.Pattern[str]) -> int:
    """Find the insertion point for a section (the end of its content).

    Scans forward from the marker. The section's own ``## Header`` immediately
    follows the marker, so we skip that first header and look for the *next*
    ``## `` header or the next marker — whichever comes first. Returns the
    line index of that boundary. If nothing is found, returns ``len(lines)``.

    Args:
        lines: All lines of the base template.
        marker_line: Line index of the section marker.
        marker_re: Compiled regex for detecting markers.

    Returns:
        Line index where rule content should be inserted (just before this line).
    """
    header_count = 0
    for j in range(marker_line + 1, len(lines)):
        if marker_re.match(lines[j]):
            return j
        if lines[j].startswith("## "):
            header_count += 1
            if header_count >= 2:
                return j
    return len(lines)


def insert_rules(base_content: str, rules: list[Rule], syntax: str) -> str:
    """Insert rule content at section markers.

    For each rule, finds its target section marker and appends the rule's
    content (for the given syntax type) after the existing section content.

    Args:
        base_content: Raw base template with section markers.
        rules: Rules to insert.
        syntax: Format syntax type ("markdown" or "plaintext").

    Returns:
        Assembled content with rules inserted, markers still present.

    Raises:
        ValueError: If a rule targets a section that does not exist in the
            base content.
    """
    marker_re = _MARKDOWN_MARKER if syntax == "markdown" else _PLAINTEXT_MARKER

    # Build a map of section_id → list of rule content strings.
    section_rules: dict[str, list[str]] = {}
    for rule in rules:
        section_id = rule.section
        # Verify the section exists in the base content.
        pattern = (
            f"<!-- cxp:section:{section_id} -->"
            if syntax == "markdown"
            else f"# cxp:section:{section_id}"
        )
        if pattern not in base_content:
            raise ValueError(
                f"Rule {rule.id!r} targets section {section_id!r}, "
                f"which does not exist in the base template."
            )
        content = rule.content.get(syntax, "")
        if content:
            section_rules.setdefault(section_id, []).append(content.rstrip())

    if not section_rules:
        return base_content

    lines = base_content.split("\n")

    # Find marker positions.
    marker_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = marker_re.match(line)
        if m:
            marker_positions.append((i, m.group(1)))

    # Compute insertion point for each section that has rules.
    insert_at: dict[int, list[str]] = {}
    for line_num, section_id in marker_positions:
        if section_id not in section_rules:
            continue
        end = _find_section_end(lines, line_num, marker_re)
        insert_at.setdefault(end, []).extend(section_rules[section_id])

    # Build output, inserting rule content at the computed positions.
    result_lines: list[str] = []
    for i, line in enumerate(lines):
        if i in insert_at:
            # Insert rules just before this boundary line.
            result_lines.append("")
            for rule_text in insert_at[i]:
                result_lines.append(rule_text)
                result_lines.append("")
        result_lines.append(line)

    # Handle insertions at end-of-file.
    if len(lines) in insert_at:
        result_lines.append("")
        for rule_text in insert_at[len(lines)]:
            result_lines.append(rule_text)
            result_lines.append("")

    return "\n".join(result_lines)


def strip_markers(content: str) -> str:
    """Remove all section markers from assembled content.

    Strips both markdown (``<!-- cxp:section:ID -->``) and plaintext
    (``# cxp:section:ID``) style markers. Also cleans up any resulting
    double-blank-lines.

    Args:
        content: Assembled content with CXP markers.

    Returns:
        Clean content with no CXP markers.
    """
    result = _ANY_MARKER.sub("", content)
    # Clean up triple+ blank lines left by marker removal.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result
