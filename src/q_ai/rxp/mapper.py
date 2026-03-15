"""Mapper from RXP validation operations to core DB runs.

Creates run records for validation operations and writes
validation detail rows to rxp_validations.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus
from q_ai.rxp.db import save_validation
from q_ai.rxp.models import ValidationResult


def persist_validation(
    result: ValidationResult,
    profile_id: str | None,
    top_k: int,
    db_path: Path | None = None,
    run_id: str | None = None,
) -> str:
    """Persist a validation result to the database.

    Creates a run record and writes the validation detail row.

    Args:
        result: ValidationResult from the validation engine.
        profile_id: Domain profile ID used, or None for custom corpus.
        top_k: Number of retrieval results per query.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.
        run_id: Optional pre-created run ID from the orchestrator.
            When provided, skips creating a new run record.

    Returns:
        The run ID for the validation operation.
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
                     config, status, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "rxp",
                    f"validate-{result.model_id}",
                    None,
                    None,
                    None,
                    int(RunStatus.COMPLETED),
                    now_iso,
                    now_iso,
                ),
            )

    save_validation(
        run_id=run_id,
        result=result,
        profile_id=profile_id,
        top_k=top_k,
        db_path=db_path,
    )

    return run_id
