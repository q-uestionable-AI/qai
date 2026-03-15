"""AGENTS.md format -- cross-assistant standard."""

from __future__ import annotations

from q_ai.cxp.formats import register
from q_ai.cxp.models import AssistantFormat

AGENTS_MD = AssistantFormat(
    id="agents-md",
    filename="AGENTS.md",
    assistant="Multi-assistant",
    syntax="markdown",
)

register(AGENTS_MD)
