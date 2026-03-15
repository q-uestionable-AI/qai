"""copilot-instructions.md format for GitHub Copilot."""

from __future__ import annotations

from q_ai.cxp.formats import register
from q_ai.cxp.models import AssistantFormat

COPILOT_INSTRUCTIONS = AssistantFormat(
    id="copilot-instructions",
    filename=".github/copilot-instructions.md",
    assistant="GitHub Copilot",
    syntax="markdown",
)

register(COPILOT_INSTRUCTIONS)
