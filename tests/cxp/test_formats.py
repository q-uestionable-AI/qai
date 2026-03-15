"""Tests for assistant format registry."""

from __future__ import annotations

from q_ai.cxp.formats import get_format, list_formats
from q_ai.cxp.models import AssistantFormat


class TestFormatRegistry:
    def test_list_formats_returns_six(self) -> None:
        formats = list_formats()
        assert len(formats) == 6

    def test_list_formats_returns_format_instances(self) -> None:
        formats = list_formats()
        for fmt in formats:
            assert isinstance(fmt, AssistantFormat)

    def test_get_claude_md(self) -> None:
        fmt = get_format("claude-md")
        assert fmt is not None
        assert fmt.id == "claude-md"
        assert fmt.filename == "CLAUDE.md"
        assert fmt.assistant == "Claude Code"
        assert fmt.syntax == "markdown"

    def test_get_cursorrules(self) -> None:
        fmt = get_format("cursorrules")
        assert fmt is not None
        assert fmt.id == "cursorrules"
        assert fmt.filename == ".cursorrules"
        assert fmt.assistant == "Cursor"
        assert fmt.syntax == "plaintext"

    def test_get_copilot_instructions(self) -> None:
        fmt = get_format("copilot-instructions")
        assert fmt is not None
        assert fmt.id == "copilot-instructions"
        assert fmt.filename == ".github/copilot-instructions.md"
        assert fmt.assistant == "GitHub Copilot"
        assert fmt.syntax == "markdown"

    def test_get_agents_md(self) -> None:
        fmt = get_format("agents-md")
        assert fmt is not None
        assert fmt.id == "agents-md"
        assert fmt.filename == "AGENTS.md"
        assert fmt.assistant == "Multi-assistant"
        assert fmt.syntax == "markdown"

    def test_get_gemini_md(self) -> None:
        fmt = get_format("gemini-md")
        assert fmt is not None
        assert fmt.id == "gemini-md"
        assert fmt.filename == "GEMINI.md"
        assert fmt.assistant == "Gemini Code Assist"
        assert fmt.syntax == "markdown"

    def test_get_windsurfrules(self) -> None:
        fmt = get_format("windsurfrules")
        assert fmt is not None
        assert fmt.id == "windsurfrules"
        assert fmt.filename == ".windsurfrules"
        assert fmt.assistant == "Windsurf"
        assert fmt.syntax == "plaintext"

    def test_get_unknown_returns_none(self) -> None:
        assert get_format("nonexistent") is None
