"""Callback listener infrastructure for IPI hit processing.

Core scoring and hit recording logic. Confidence scoring determines the
likelihood that a callback originated from a genuine AI agent execution.
HIGH and MEDIUM confidence hits are promoted to findings in the shared
findings table for cross-module reporting.
"""

from __future__ import annotations

import re
from pathlib import Path

from q_ai.core.db import create_finding, create_run, get_connection, update_run_status
from q_ai.core.models import RunStatus, Severity
from q_ai.ipi.db import get_campaign, save_hit
from q_ai.ipi.models import Hit, HitConfidence

# User-Agent patterns that suggest programmatic HTTP clients (not browsers/scanners)
_PROGRAMMATIC_UA_PATTERNS = re.compile(
    r"python-requests|httpx|aiohttp|urllib|curl|wget|node-fetch|"
    r"axios|got/|undici|fetch|llm|openai|langchain",
    re.IGNORECASE,
)


def score_confidence(token_valid: bool, user_agent: str) -> HitConfidence:
    """Score hit confidence based on token validity and User-Agent analysis.

    Confidence rubric:
        HIGH: Valid campaign token present — strong proof of agent execution.
        MEDIUM: No/invalid token, but User-Agent matches known programmatic
            HTTP clients (python-requests, httpx, curl, etc.).
        LOW: No/invalid token and browser or scanner User-Agent.

    Args:
        token_valid: Whether the campaign authentication token matched.
        user_agent: HTTP User-Agent header from the request.

    Returns:
        HitConfidence level for the callback.
    """
    if token_valid:
        return HitConfidence.HIGH
    if _PROGRAMMATIC_UA_PATTERNS.search(user_agent):
        return HitConfidence.MEDIUM
    return HitConfidence.LOW


def record_hit(hit: Hit, db_path: Path | None = None) -> None:
    """Persist a hit and create a finding for HIGH or MEDIUM confidence callbacks.

    Saves the hit to ipi_hits, then for HIGH and MEDIUM confidence hits looks
    up the associated campaign to find the run_id. If no run_id exists (legacy
    or standalone campaign), an ad-hoc run record is created automatically so
    the findings table FK constraint is satisfied.

    Severity mapping:
        HIGH → Severity.CRITICAL
        MEDIUM → Severity.HIGH

    Args:
        hit: Hit object to save and potentially promote to a finding.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.
    """
    save_hit(hit, db_path=db_path)

    if hit.confidence not in (HitConfidence.HIGH, HitConfidence.MEDIUM):
        return

    campaign = get_campaign(hit.uuid, db_path=db_path)

    severity = Severity.CRITICAL if hit.confidence == HitConfidence.HIGH else Severity.HIGH

    with get_connection(db_path) as conn:
        run_id = campaign.run_id if campaign is not None else None

        if run_id is None:
            run_id = create_run(
                conn,
                module="ipi",
                name=f"callback-hit-{hit.uuid[:8]}",
            )
            update_run_status(conn, run_id, RunStatus.COMPLETED)

        create_finding(
            conn,
            run_id=run_id,
            module="ipi",
            category="callback_hit",
            severity=severity,
            title=f"IPI callback: {hit.uuid[:8]}",
            description=(
                f"Callback hit received for campaign {hit.uuid}. "
                f"Confidence: {hit.confidence.value}. "
                f"Source IP: {hit.source_ip}. "
                f"User-Agent: {hit.user_agent}."
            ),
        )


__all__ = [
    "record_hit",
    "score_confidence",
]
