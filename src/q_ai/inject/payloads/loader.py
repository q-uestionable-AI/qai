"""Payload template loader -- discovers, parses, and filters YAML templates."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from q_ai.inject.models import InjectionTechnique, PayloadTemplate

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("name", "technique", "description", "tool_name", "tool_description")


def discover_templates(template_dir: Path | None = None) -> list[Path]:
    """Find all YAML template files in the given directory.

    Args:
        template_dir: Directory to search. Defaults to the built-in
            ``payloads/templates`` directory shipped with the package.

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


def load_template(path: Path) -> list[PayloadTemplate]:
    """Parse a single YAML file into PayloadTemplate objects.

    Each YAML file is expected to contain a list of mapping objects. Entries
    that are missing required fields or have an invalid ``technique`` value
    are logged as warnings and skipped.

    Args:
        path: Path to the YAML template file.

    Returns:
        List of successfully parsed PayloadTemplate objects. Returns an
        empty list when the file cannot be parsed.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        logger.warning("Failed to load template file %s: %s", path, exc)
        return []

    if isinstance(raw, dict):
        raw = [raw]
    elif not isinstance(raw, list):
        logger.warning(
            "Template file %s has unexpected top-level type: %s", path, type(raw).__name__
        )
        return []

    templates: list[PayloadTemplate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("Skipping non-dict entry in %s", path)
            continue

        # Check required fields
        missing = [f for f in _REQUIRED_FIELDS if f not in entry]
        if missing:
            logger.warning(
                "Skipping entry in %s: missing required fields %s",
                path,
                missing,
            )
            continue

        # Map technique string to enum
        technique_str = entry["technique"]
        try:
            technique = InjectionTechnique(technique_str)
        except ValueError:
            logger.warning(
                "Skipping entry '%s' in %s: invalid technique '%s'",
                entry.get("name", "<unknown>"),
                path,
                technique_str,
            )
            continue

        templates.append(
            PayloadTemplate(
                name=entry["name"],
                technique=technique,
                description=entry["description"],
                owasp_ids=entry.get("owasp_ids", []),
                target_agents=entry.get("target_agents", []),
                tool_name=entry["tool_name"],
                tool_description=entry["tool_description"],
                tool_params=entry.get("tool_params", {}),
                tool_response=entry.get("tool_response", ""),
                test_query=entry.get("test_query", ""),
            )
        )

    return templates


def load_all_templates(template_dir: Path | None = None) -> list[PayloadTemplate]:
    """Discover and load all templates from a directory.

    Calls :func:`discover_templates` then :func:`load_template` on each
    discovered file and returns a flat list of all templates.

    Args:
        template_dir: Directory to search. Defaults to the built-in
            templates directory.

    Returns:
        Flat list of all successfully parsed PayloadTemplate objects.
    """
    templates: list[PayloadTemplate] = []
    for path in discover_templates(template_dir):
        templates.extend(load_template(path))
    return templates


def filter_templates(
    templates: list[PayloadTemplate],
    technique: InjectionTechnique | None = None,
    target_agent: str | None = None,
) -> list[PayloadTemplate]:
    """Filter templates by technique and/or target agent.

    Both filters are combined with AND logic when both are provided.

    A template matches the ``target_agent`` filter if the agent appears in the
    template's ``target_agents`` list **or** the template has an empty
    ``target_agents`` list (meaning it is universal and applies to all agents).

    Args:
        templates: Templates to filter.
        technique: If provided, only include templates with this technique.
        target_agent: If provided, only include templates that target this
            agent or are universal (empty ``target_agents``).

    Returns:
        Filtered list of templates.
    """
    result = templates

    if technique is not None:
        result = [t for t in result if t.technique == technique]

    if target_agent is not None:
        result = [t for t in result if not t.target_agents or target_agent in t.target_agents]

    return result
