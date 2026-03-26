"""SARIF 2.1.0 JSON parser."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from q_ai import __version__
from q_ai.core.models import Severity
from q_ai.imports.models import ImportedFinding, ImportResult

logger = logging.getLogger(__name__)

_SARIF_VERSION = "2.1.0"

# security-severity thresholds (CVSS-style).
_SEC_SEV_CRITICAL = 9.0
_SEC_SEV_HIGH = 7.0
_SEC_SEV_MEDIUM = 4.0

# SARIF level → Severity (fallback when no security-severity property).
_LEVEL_MAP: dict[str, Severity] = {
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "note": Severity.LOW,
    "none": Severity.INFO,
}


def _severity_from_properties(properties: dict, level: str) -> Severity:
    """Derive severity, preferring ``security-severity`` when present."""
    sec_sev = properties.get("security-severity")
    if sec_sev is not None:
        try:
            score = float(sec_sev)
        except (TypeError, ValueError):
            return _LEVEL_MAP.get(level, Severity.MEDIUM)
        if score >= _SEC_SEV_CRITICAL:
            return Severity.CRITICAL
        if score >= _SEC_SEV_HIGH:
            return Severity.HIGH
        if score >= _SEC_SEV_MEDIUM:
            return Severity.MEDIUM
        return Severity.LOW
    return _LEVEL_MAP.get(level, Severity.MEDIUM)


def _extract_taxonomy(result: dict, rule_meta: dict) -> dict[str, str]:
    """Collect tags/metadata for original_taxonomy preservation."""
    taxonomy: dict[str, str] = {}
    props = result.get("properties", {})
    tags = props.get("tags")
    if tags:
        taxonomy["tags"] = json.dumps(tags) if isinstance(tags, list) else str(tags)
    rule_tags = rule_meta.get("properties", {}).get("tags")
    if rule_tags and "tags" not in taxonomy:
        taxonomy["tags"] = json.dumps(rule_tags) if isinstance(rule_tags, list) else str(rule_tags)
    return taxonomy


def _build_rule_index(run: dict) -> dict[str, dict]:
    """Build a lookup from ruleId to rule metadata."""
    driver = run.get("tool", {}).get("driver", {})
    rules = driver.get("rules", [])
    if not isinstance(rules, list):
        return {}
    index: dict[str, dict] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id")
        if isinstance(rule_id, str) and rule_id:
            index[rule_id] = rule
    return index


def _parse_run(
    run: dict,
) -> tuple[list[ImportedFinding], list[str], str, str | None]:
    """Parse a single SARIF run into findings.

    Returns:
        ``(findings, errors, tool_name, tool_version)``
    """
    driver = run.get("tool", {}).get("driver", {})
    tool_name = driver.get("name", "unknown")
    tool_version = driver.get("semanticVersion") or driver.get("version")
    rule_index = _build_rule_index(run)

    findings: list[ImportedFinding] = []
    errors: list[str] = []

    results_list = run.get("results", [])
    if not isinstance(results_list, list):
        return findings, ["'results' is not an array"], tool_name, tool_version

    for result in results_list:
        if not isinstance(result, dict):
            errors.append("Skipping non-dict result entry")
            continue
        rule_id = result.get("ruleId")
        if rule_id is not None and not isinstance(rule_id, str):
            rule_id = str(rule_id)
        rule_meta = rule_index.get(rule_id, {}) if rule_id else {}
        level = result.get("level", "warning")
        properties = result.get("properties", {})

        message_obj = result.get("message", {})
        title = message_obj.get("text", rule_id or "Untitled finding")
        if len(title) > 200:
            title = title[:197] + "..."

        taxonomy = _extract_taxonomy(result, rule_meta)

        findings.append(
            ImportedFinding(
                category=rule_id or "unknown",
                severity=_severity_from_properties(properties, level),
                title=title,
                description=rule_meta.get("fullDescription", {}).get("text"),
                source_tool="sarif",
                source_tool_version=tool_version,
                original_id=rule_id,
                original_taxonomy=taxonomy,
                raw_evidence=json.dumps(result, default=str),
            )
        )

    return findings, errors, tool_name, tool_version


def parse_sarif(path: Path) -> ImportResult:
    """Parse a SARIF 2.1.0 JSON file.

    Args:
        path: Path to the SARIF JSON file.

    Returns:
        An :class:`ImportResult` with parsed findings.

    Raises:
        ValueError: If the file is not valid SARIF 2.1.0.
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc

    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object in {path.name}")

    version = data.get("version")
    schema = data.get("$schema", "")
    if version != _SARIF_VERSION and _SARIF_VERSION not in schema:
        raise ValueError(
            f"Unsupported SARIF version in {path.name}: "
            f"expected {_SARIF_VERSION}, got version={version!r}"
        )

    runs = data.get("runs", [])
    if not isinstance(runs, list):
        raise TypeError(f"Expected 'runs' array in {path.name}")

    all_findings: list[ImportedFinding] = []
    all_errors: list[str] = []
    tool_name: str = "unknown"
    tool_version: str | None = None

    for run in runs:
        if not isinstance(run, dict):
            all_errors.append("Skipping non-dict run entry")
            continue
        findings, errors, run_tool, run_version = _parse_run(run)
        all_findings.extend(findings)
        all_errors.extend(errors)
        # Use the first run's tool info as the primary.
        if tool_name == "unknown" and run_tool != "unknown":
            tool_name = run_tool
        if tool_version is None and run_version is not None:
            tool_version = run_version

    return ImportResult(
        findings=all_findings,
        tool_name=tool_name,
        tool_version=tool_version,
        parser_version=__version__,
        source_file=path.name,
        errors=all_errors,
    )
