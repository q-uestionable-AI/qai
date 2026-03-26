"""PyRIT JSON conversation export parser."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from q_ai import __version__
from q_ai.core.models import Severity
from q_ai.imports.models import ImportedFinding, ImportResult

logger = logging.getLogger(__name__)


def _severity_from_score(score_value: object, score_type: str) -> tuple[Severity, str | None]:
    """Map a PyRIT score to a severity level.

    Returns:
        A ``(severity, warning)`` tuple.  *warning* is ``None`` when the
        mapping is unambiguous.
    """
    # true/false scoring
    if score_type == "true_false":
        if str(score_value).lower() == "true":
            return Severity.HIGH, None
        return Severity.INFO, None

    # Likert scale 1-5
    if score_type == "likert":
        try:
            val = int(str(score_value))
        except (TypeError, ValueError):
            return Severity.MEDIUM, f"Non-numeric Likert score: {score_value}"
        severity_map = {
            5: Severity.CRITICAL,
            4: Severity.HIGH,
            3: Severity.MEDIUM,
            2: Severity.LOW,
            1: Severity.INFO,
        }
        return severity_map.get(val, Severity.MEDIUM), None

    # Unknown scoring type — default to MEDIUM.
    return Severity.MEDIUM, f"Unknown score type '{score_type}' — defaulting to MEDIUM"


def _last_assistant_message(messages: list[dict]) -> str:
    """Extract the last assistant response from a conversation."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content: str = str(msg.get("content", ""))
            if len(content) > 500:
                return content[:497] + "..."
            return content
    return "(no assistant response)"


def _build_finding(conversation: dict) -> tuple[ImportedFinding | None, list[str]]:
    """Convert a scored PyRIT conversation to an ImportedFinding.

    Returns:
        A ``(finding, warnings)`` tuple.  *finding* is ``None`` when the
        conversation has no scoring results.
    """
    warnings: list[str] = []

    scoring = conversation.get("scoring")
    if not scoring:
        return None, []

    # scoring can be a list or a single dict.
    if isinstance(scoring, dict):
        scoring = [scoring]
    if not isinstance(scoring, list) or not scoring:
        return None, []

    # Use the first scoring result.
    score = scoring[0]
    score_value = score.get("score_value")
    score_type = score.get("score_type", "")
    if score_value is None:
        return None, ["Conversation has scoring but no score_value"]

    severity, sev_warning = _severity_from_score(score_value, score_type)
    if sev_warning:
        warnings.append(sev_warning)

    messages = conversation.get("messages", [])
    labels = conversation.get("labels", {})
    if not isinstance(labels, dict):
        labels = {}
    strategy = labels.get("attack_strategy", labels.get("strategy", "unknown"))
    conversation_id = conversation.get("conversation_id", conversation.get("id"))

    description = _last_assistant_message(messages)

    return ImportedFinding(
        category=str(strategy),
        severity=severity,
        title=f"PyRIT: {strategy}",
        description=description,
        source_tool="pyrit",
        source_tool_version=None,
        original_id=str(conversation_id) if conversation_id else None,
        original_taxonomy=labels,
        raw_evidence=json.dumps(conversation, default=str),
    ), warnings


def parse_pyrit(path: Path) -> ImportResult:
    """Parse a PyRIT JSON conversation export.

    Args:
        path: Path to the PyRIT JSON file.

    Returns:
        An :class:`ImportResult` with parsed findings.

    Raises:
        ValueError: If the file is not valid JSON or not a list.
    """
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path.name}: {exc}") from exc

    if not isinstance(data, list):
        raise TypeError(f"Expected a JSON array in {path.name}, got {type(data).__name__}")

    findings: list[ImportedFinding] = []
    errors: list[str] = []

    for idx, conversation in enumerate(data):
        if not isinstance(conversation, dict):
            errors.append(f"Entry {idx}: expected dict, got {type(conversation).__name__}")
            continue

        finding, warnings = _build_finding(conversation)
        errors.extend(warnings)
        if finding is not None:
            findings.append(finding)

    return ImportResult(
        findings=findings,
        tool_name="pyrit",
        tool_version=None,
        parser_version=__version__,
        source_file=path.name,
        errors=errors,
    )
