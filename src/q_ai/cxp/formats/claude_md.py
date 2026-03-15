"""CLAUDE.md format for Claude Code."""

from __future__ import annotations

from q_ai.cxp.formats import register
from q_ai.cxp.models import AssistantFormat

CLAUDE_MD = AssistantFormat(
    id="claude-md",
    filename="CLAUDE.md",
    assistant="Claude Code",
    syntax="markdown",
)

register(CLAUDE_MD)
