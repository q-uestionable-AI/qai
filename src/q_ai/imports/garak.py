"""Garak JSONL report parser."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from q_ai import __version__
from q_ai.core.models import Severity
from q_ai.imports.models import ImportedFinding, ImportResult
from q_ai.imports.taxonomy import resolve_bridge

logger = logging.getLogger(__name__)

_OWASP_LLM_FRAMEWORK = "owasp_llm_top10"

# Severity thresholds based on Garak pass rates.
_PASS_RATE_CRITICAL = 0.25
_PASS_RATE_HIGH = 0.50
_PASS_RATE_MEDIUM = 0.75
_PASS_RATE_LOW = 1.00


def _severity_from_pass_rate(pass_rate: float) -> Severity:
    """Map a Garak detector pass rate to a severity level."""
    if pass_rate >= _PASS_RATE_LOW:
        return Severity.INFO
    if pass_rate >= _PASS_RATE_MEDIUM:
        return Severity.LOW
    if pass_rate >= _PASS_RATE_HIGH:
        return Severity.MEDIUM
    if pass_rate >= _PASS_RATE_CRITICAL:
        return Severity.HIGH
    return Severity.CRITICAL


def _extract_taxonomy(entry: dict) -> dict[str, str]:
    """Pull taxonomy IDs from a Garak eval entry."""
    taxonomy: dict[str, str] = {}
    # Garak stores probe-level taxonomy tags in the entry.
    for key in ("owasp_llm", "avid"):
        val = entry.get(key)
        if val:
            taxonomy[key] = str(val)
    return taxonomy


def _apply_bridge(taxonomy: dict[str, str]) -> str | None:
    """Return a qai category via taxonomy bridge, if one exists."""
    owasp_id = taxonomy.get("owasp_llm")
    if not owasp_id:
        return None
    bridge = resolve_bridge(_OWASP_LLM_FRAMEWORK, owasp_id)
    if bridge.confidence != "none":
        return bridge.qai_category
    return None


def _build_finding(entry: dict, garak_version: str | None) -> ImportedFinding:
    """Convert a single Garak eval entry to an ImportedFinding."""
    probe = entry.get("probe", "unknown_probe")
    detector = entry.get("detector", "unknown_detector")
    try:
        passed = int(entry.get("passed", 0))
    except (TypeError, ValueError):
        passed = 0
    try:
        total = int(entry.get("total", 0))
    except (TypeError, ValueError):
        total = 0
    pass_rate = passed / total if total > 0 else 1.0

    taxonomy = _extract_taxonomy(entry)
    bridged_category = _apply_bridge(taxonomy)
    category = bridged_category or probe

    description = f"Detector: {detector} — {passed}/{total} passed ({pass_rate:.0%})"

    return ImportedFinding(
        category=category,
        severity=_severity_from_pass_rate(pass_rate),
        title=f"{probe} [{detector}]",
        description=description,
        source_tool="garak",
        source_tool_version=garak_version,
        original_id=entry.get("eval_id") or entry.get("id"),
        original_taxonomy=taxonomy,
        raw_evidence=json.dumps(entry, default=str),
    )


def _parse_jsonl_line(raw_line: str, line_no: int) -> tuple[dict | None, str | None]:
    """Decode a single JSONL line into a dict.

    Returns:
        ``(entry, error)`` — one of the two will be ``None``.
    """
    stripped = raw_line.strip()
    if not stripped:
        return None, None
    try:
        entry = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"Line {line_no}: invalid JSON — {exc}"
    if not isinstance(entry, dict):
        return None, (f"Line {line_no}: JSON is not an object (type={type(entry).__name__})")
    return entry, None


def parse_garak(path: Path) -> ImportResult:
    """Parse a Garak JSONL report file.

    Args:
        path: Path to the Garak JSONL report.

    Returns:
        An :class:`ImportResult` with parsed findings.

    Raises:
        ValueError: If the file does not contain a recognised Garak
            ``start_run setup`` entry.
    """
    findings: list[ImportedFinding] = []
    errors: list[str] = []
    garak_version: str | None = None
    found_setup = False
    attempt_count = 0

    with path.open(encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            entry, error = _parse_jsonl_line(raw_line, line_no)
            if error:
                errors.append(error)
                continue
            if entry is None:
                continue

            entry_type = entry.get("entry_type", "")

            # Detect setup entry.
            if entry_type == "start_run setup":
                found_setup = True
                garak_version = entry.get("garak_version")
                if garak_version:
                    logger.info("Detected Garak version: %s", garak_version)
                continue

            # Skip attempt entries — count only.
            if entry_type == "attempt":
                attempt_count += 1
                continue

            # Process eval entries.
            if entry_type == "eval":
                findings.append(_build_finding(entry, garak_version))
                continue

    if not found_setup:
        raise ValueError(
            f"File does not appear to be a Garak report — "
            f"no 'start_run setup' entry found in {path.name}"
        )

    if attempt_count:
        logger.info("Skipped %d attempt entries (stored as evidence summary)", attempt_count)

    return ImportResult(
        findings=findings,
        tool_name="garak",
        tool_version=garak_version,
        parser_version=__version__,
        source_file=path.name,
        errors=errors,
    )
