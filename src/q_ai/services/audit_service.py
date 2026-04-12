"""Audit service — query logic for audit scans and their evidence."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from q_ai.core.mitigation import MitigationGuidance
from q_ai.core.models import Evidence

logger = logging.getLogger(__name__)


def get_audit_run_detail(
    conn: sqlite3.Connection,
    audit_child: Any,
    findings: list[Any],
) -> tuple[dict[str, Any] | None, list[Any], dict[str, list[Any]]]:
    """Load audit-specific data: scan record, findings, and evidence map.

    Args:
        conn: Active database connection.
        audit_child: The audit child run, or None.
        findings: All findings for the parent run.

    Returns:
        Tuple of (audit_scan dict or None, audit_findings list,
        audit_evidence_map keyed by finding ID).
    """
    if not audit_child:
        return None, [], {}

    row = conn.execute(
        "SELECT * FROM audit_scans WHERE run_id = ? LIMIT 1",
        (audit_child.id,),
    ).fetchone()
    audit_scan = dict(row) if row else None

    audit_findings = [f for f in findings if f.run_id == audit_child.id]
    audit_evidence_map: dict[str, list[Any]] = {}

    for af in audit_findings:
        audit_evidence_map[af.id] = []
        af.mitigation_guidance = None
        if af.mitigation:
            try:
                af.mitigation_guidance = MitigationGuidance.from_dict(af.mitigation)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to parse mitigation guidance for finding %s: %s",
                    af.id,
                    exc,
                )

    if audit_findings:
        finding_ids = [af.id for af in audit_findings]
        ph = ", ".join("?" for _ in finding_ids)
        ev_rows = conn.execute(
            f"SELECT * FROM evidence WHERE finding_id IN ({ph}) ORDER BY created_at DESC",  # noqa: S608
            finding_ids,
        ).fetchall()
        for ev_row in ev_rows:
            ev = Evidence.from_row(dict(ev_row))
            if ev.finding_id in audit_evidence_map:
                audit_evidence_map[ev.finding_id].append(ev)

    return audit_scan, audit_findings, audit_evidence_map
