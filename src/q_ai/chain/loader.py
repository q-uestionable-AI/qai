"""Chain definition loader -- discovers, parses, and validates YAML chain files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from q_ai.chain.models import ChainCategory, ChainDefinition, ChainStep

_REQUIRED_TOP_LEVEL = ("id", "name", "category", "description", "steps")
_REQUIRED_STEP = ("id", "name", "module", "technique")


class ChainValidationError(Exception):
    """Raised when a chain definition fails structural validation."""


def discover_chains(template_dir: Path | None = None) -> list[Path]:
    """Find all YAML chain files in the given directory.

    Args:
        template_dir: Directory to search. Defaults to the built-in
            ``chain/templates`` directory shipped with the package.

    Returns:
        Sorted list of paths to ``.yaml`` and ``.yml`` files.
    """
    if template_dir is None:
        template_dir = Path(__file__).parent / "templates"

    paths: list[Path] = []
    if template_dir.is_dir():
        for ext in ("*.yaml", "*.yml"):
            paths.extend(template_dir.glob(ext))
    return sorted(paths)


def load_chain(path: Path) -> ChainDefinition:
    """Parse a single YAML file into a ChainDefinition.

    Each YAML file is expected to contain a single mapping (dict) at the top
    level describing one attack chain.

    Args:
        path: Path to the YAML chain definition file.

    Returns:
        A fully-validated :class:`~q_ai.chain.models.ChainDefinition`.

    Raises:
        ChainValidationError: If the file cannot be parsed, is missing required
            fields, has an invalid category, or contains duplicate step IDs.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ChainValidationError(f"Failed to read or parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ChainValidationError(
            f"{path}: expected a YAML mapping at top level, got {type(raw).__name__}"
        )

    # Validate required top-level fields
    missing = [f for f in _REQUIRED_TOP_LEVEL if f not in raw]
    if missing:
        raise ChainValidationError(f"{path}: missing required top-level fields: {missing}")

    # Validate category
    category_str: str = raw["category"]
    try:
        category = ChainCategory(category_str)
    except ValueError:
        valid = [c.value for c in ChainCategory]
        raise ChainValidationError(
            f"{path}: invalid category '{category_str}'. Must be one of: {valid}"
        ) from None

    # Validate steps list
    raw_steps: Any = raw["steps"]
    if not isinstance(raw_steps, list):
        raise ChainValidationError(
            f"{path}: 'steps' must be a list, got {type(raw_steps).__name__}"
        )

    steps: list[ChainStep] = []
    seen_step_ids: set[str] = set()

    for i, entry in enumerate(raw_steps):
        if not isinstance(entry, dict):
            raise ChainValidationError(
                f"{path}: step {i} is not a mapping (got {type(entry).__name__})"
            )

        missing_step = [f for f in _REQUIRED_STEP if f not in entry]
        if missing_step:
            raise ChainValidationError(f"{path}: step {i} missing required fields: {missing_step}")

        step_id: str = entry["id"]
        if step_id in seen_step_ids:
            raise ChainValidationError(f"{path}: Duplicate step id '{step_id}' found in chain")
        seen_step_ids.add(step_id)

        steps.append(
            ChainStep(
                id=step_id,
                name=entry["name"],
                module=entry["module"],
                technique=entry["technique"],
                trust_boundary=entry.get("trust_boundary"),
                on_success=entry.get("on_success"),
                on_failure=entry.get("on_failure", "abort"),
                terminal=entry.get("terminal", False),
                inputs=entry.get("inputs", {}),
                relevant_categories=entry.get("relevant_categories", []),
            )
        )

    return ChainDefinition(
        id=raw["id"],
        name=raw["name"],
        category=category,
        description=raw["description"],
        steps=steps,
        entry_cves=raw.get("entry_cves", []),
    )


def load_all_chains(template_dir: Path | None = None) -> list[ChainDefinition]:
    """Discover and load all chain definitions from a directory.

    Calls :func:`discover_chains` then :func:`load_chain` on each discovered
    file and returns a flat list of all chain definitions. Raises
    :class:`ChainValidationError` if any file fails validation or if duplicate
    chain IDs are found across files.

    Args:
        template_dir: Directory to search. Defaults to the built-in
            templates directory.

    Returns:
        List of all successfully parsed :class:`~q_ai.chain.models.ChainDefinition`
        objects.

    Raises:
        ChainValidationError: If any chain file fails validation or duplicate
            chain IDs are detected across files.
    """
    chains: list[ChainDefinition] = []
    seen_ids: set[str] = set()

    for path in discover_chains(template_dir):
        chain = load_chain(path)
        if chain.id in seen_ids:
            raise ChainValidationError(f"Duplicate chain id '{chain.id}' found in {path}")
        seen_ids.add(chain.id)
        chains.append(chain)

    return chains
