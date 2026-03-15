"""Format registry -- assistant instruction file format definitions.

Formats describe the instruction file each coding assistant reads.
Each format module registers itself at import time.
"""

from __future__ import annotations

from q_ai.cxp.models import AssistantFormat

_registry: dict[str, AssistantFormat] = {}


def register(fmt: AssistantFormat) -> None:
    """Register a format in the global registry.

    Args:
        fmt: The format to register.
    """
    _registry[fmt.id] = fmt


def get_format(format_id: str) -> AssistantFormat | None:
    """Look up a format by ID.

    Args:
        format_id: The format identifier.

    Returns:
        The format, or None if not found.
    """
    return _registry.get(format_id)


def list_formats() -> list[AssistantFormat]:
    """Return all registered formats.

    Returns:
        List of all formats.
    """
    return list(_registry.values())


# Auto-import to trigger registration
from q_ai.cxp.formats import (  # noqa: E402, F401
    agents_md,
    claude_md,
    copilot_instructions,
    cursorrules,
    gemini_md,
    windsurfrules,
)
