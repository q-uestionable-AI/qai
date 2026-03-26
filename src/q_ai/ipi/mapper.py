"""Mapper from IPI generate operations to core DB runs.

Creates run records for generate operations and links generated
campaigns (ipi_payloads) to the run via run_id updates.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus
from q_ai.ipi.models import Campaign


def persist_generate(
    campaigns: list[Campaign],
    db_path: Path | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> str:
    """Persist a generate operation to the database.

    Creates a run record and links all generated campaigns to it
    by updating their run_id in ipi_payloads.

    Args:
        campaigns: List of Campaign objects from generation.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.
        run_id: Optional pre-created run ID from the orchestrator.
            When provided, skips creating a new run record.
        source: Optional provenance tag (e.g. "web", "cli").

    Returns:
        The run ID for the generate operation.
    """
    skip_run_insert = run_id is not None
    if run_id is None:
        run_id = uuid.uuid4().hex
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    with get_connection(db_path) as conn:
        if not skip_run_insert:
            conn.execute(
                """
                INSERT INTO runs
                    (id, module, name, target_id, parent_run_id,
                     config, status, started_at, finished_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "ipi",
                    f"generate-{len(campaigns)}-payloads",
                    None,
                    None,
                    None,
                    int(RunStatus.COMPLETED),
                    now_iso,
                    now_iso,
                    source,
                ),
            )

        for campaign in campaigns:
            conn.execute(
                "UPDATE ipi_payloads SET run_id = ? WHERE id = ?",
                (run_id, campaign.id),
            )

    return run_id
