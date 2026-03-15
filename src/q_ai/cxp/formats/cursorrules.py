""".cursorrules format for Cursor."""

from __future__ import annotations

from q_ai.cxp.formats import register
from q_ai.cxp.models import AssistantFormat

CURSORRULES = AssistantFormat(
    id="cursorrules",
    filename=".cursorrules",
    assistant="Cursor",
    syntax="plaintext",
)

register(CURSORRULES)
