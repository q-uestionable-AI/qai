""".windsurfrules format for Windsurf."""

from __future__ import annotations

from q_ai.cxp.formats import register
from q_ai.cxp.models import AssistantFormat

WINDSURFRULES = AssistantFormat(
    id="windsurfrules",
    filename=".windsurfrules",
    assistant="Windsurf",
    syntax="plaintext",
)

register(WINDSURFRULES)
