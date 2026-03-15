"""GEMINI.md format for Gemini Code Assist."""

from __future__ import annotations

from q_ai.cxp.formats import register
from q_ai.cxp.models import AssistantFormat

GEMINI_MD = AssistantFormat(
    id="gemini-md",
    filename="GEMINI.md",
    assistant="Gemini Code Assist",
    syntax="markdown",
)

register(GEMINI_MD)
