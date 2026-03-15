"""Mapper from Campaign to core DB for persistence.

Bridges the inject campaign domain to the core persistence domain
so campaign results are stored in the unified SQLite database.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus, Severity
from q_ai.inject.models import Campaign, InjectionOutcome

_OUTCOME_SEVERITY: dict[InjectionOutcome, Severity] = {
    InjectionOutcome.FULL_COMPLIANCE: Severity.CRITICAL,
    InjectionOutcome.PARTIAL_COMPLIANCE: Severity.HIGH,
    InjectionOutcome.REFUSAL_WITH_LEAK: Severity.MEDIUM,
}


def persist_campaign(
    campaign: Campaign,
    db_path: Path | None = None,
) -> str:
    """Persist a Campaign to the database.

    Creates a run, inject_results rows, and findings for security-relevant
    outcomes. Returns the run ID.

    Args:
        campaign: Completed campaign from the executor.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.

    Returns:
        The run ID for the persisted campaign.
    """
    run_id = uuid.uuid4().hex
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runs
                (id, module, name, target_id, parent_run_id,
                 config, status, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "inject",
                campaign.name,
                None,
                None,
                None,
                int(RunStatus.COMPLETED),
                campaign.started_at.isoformat() if campaign.started_at else now_iso,
                campaign.finished_at.isoformat() if campaign.finished_at else now_iso,
            ),
        )

        for result in campaign.results:
            result_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO inject_results
                    (id, run_id, payload_name, technique, outcome,
                     target_agent, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    run_id,
                    result.payload_name,
                    result.technique,
                    result.outcome.value,
                    result.target_agent,
                    result.evidence,
                    result.timestamp.isoformat(),
                ),
            )

            severity = _OUTCOME_SEVERITY.get(result.outcome)
            if severity is not None:
                finding_id = uuid.uuid4().hex
                evidence_preview = result.evidence[:500] if result.evidence else ""
                description = (
                    f"Target: {result.target_agent}\n"
                    f"Outcome: {result.outcome.value}\n"
                    f"Evidence: {evidence_preview}"
                )
                conn.execute(
                    """
                    INSERT INTO findings
                        (id, run_id, module, category, severity,
                         title, description, framework_ids,
                         source_ref, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finding_id,
                        run_id,
                        "inject",
                        result.technique,
                        int(severity),
                        f"{result.technique}: {result.payload_name}",
                        description,
                        None,
                        result.payload_name,
                        result.timestamp.isoformat(),
                    ),
                )

    return run_id
