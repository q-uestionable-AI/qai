"""Variable resolution for chain step inputs.

Resolves ``$step_id.artifact_name`` references in step input dictionaries
against an accumulated namespace of prior step artifacts.
"""

from __future__ import annotations

from typing import Any


def resolve_variables(
    inputs: dict[str, Any],
    artifact_namespace: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Resolve $step_id.artifact_name references in step inputs.

    Args:
        inputs: The step's inputs dict from YAML
            (e.g., {"tool_name": "$scan-injection.vulnerable_tool"}).
            Values may be strings (with optional $ references) or
            other YAML-native types (int, bool) passed through unchanged.
        artifact_namespace: Mapping of step_id -> artifacts dict,
            accumulated from prior steps.

    Returns:
        Resolved inputs dict with string variables substituted.
        Non-string values pass through unchanged.

    Raises:
        ValueError: If a variable reference cannot be resolved
            (unknown step_id or artifact_name).
    """
    if not inputs:
        return {}

    resolved: dict[str, Any] = {}
    for key, value in inputs.items():
        if isinstance(value, str) and value.startswith("$"):
            resolved[key] = _resolve_single(value, artifact_namespace)
        else:
            resolved[key] = value

    return resolved


def _resolve_single(
    ref: str,
    artifact_namespace: dict[str, dict[str, str]],
) -> str:
    """Resolve a single $step_id.artifact_name reference.

    Args:
        ref: The variable reference string (e.g., "$scan-injection.vulnerable_tool").
        artifact_namespace: The accumulated artifact namespace.

    Returns:
        The resolved artifact value.

    Raises:
        ValueError: If the step_id or artifact_name is not found.
    """
    # Strip leading $
    raw = ref[1:]

    # Split on first dot only
    if "." not in raw:
        raise ValueError(
            f"Cannot resolve '{ref}': invalid format. Expected '$step_id.artifact_name'."
        )

    step_id, artifact_name = raw.split(".", 1)

    if step_id not in artifact_namespace:
        available_steps = sorted(artifact_namespace.keys())
        raise ValueError(
            f"Cannot resolve '{ref}': step '{step_id}' not found. "
            f"Available steps: {', '.join(available_steps) if available_steps else '(none)'}"
        )

    step_artifacts = artifact_namespace[step_id]
    if artifact_name not in step_artifacts:
        available = sorted(step_artifacts.keys())
        raise ValueError(
            f"Cannot resolve '{ref}': step '{step_id}' has no artifact "
            f"'{artifact_name}'. Available: {', '.join(available)}"
        )

    return step_artifacts[artifact_name]
