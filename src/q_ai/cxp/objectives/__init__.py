"""Objective registry -- attack objective definitions.

Objectives describe what a poisoned payload tries to achieve.
Each objective module registers itself at import time.
"""

from __future__ import annotations

from q_ai.cxp.models import Objective

_registry: dict[str, Objective] = {}


def register(objective: Objective) -> None:
    """Register an objective in the global registry.

    Args:
        objective: The objective to register.
    """
    _registry[objective.id] = objective


def get_objective(objective_id: str) -> Objective | None:
    """Look up an objective by ID.

    Args:
        objective_id: The objective identifier.

    Returns:
        The objective, or None if not found.
    """
    return _registry.get(objective_id)


def list_objectives() -> list[Objective]:
    """Return all registered objectives.

    Returns:
        List of all objectives.
    """
    return list(_registry.values())


# Auto-import to trigger registration
from q_ai.cxp.objectives import (  # noqa: E402, F401
    backdoor,
    cmdexec,
    depconfusion,
    exfil,
    permescalation,
)
