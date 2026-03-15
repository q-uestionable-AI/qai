"""Mapper from CXP operations to core DB runs and findings.

Creates run records for build operations and persists test results
with automatic finding creation for hits.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus, Severity


def persist_build(
    format_id: str,
    rules_inserted: list[str],
    repo_dir: str,
    db_path: Path | None = None,
    run_id: str | None = None,
) -> str:
    """Persist a CXP build operation to the database.

    Creates a run record with module="cxp" and status=COMPLETED.

    Args:
        format_id: The format used for the build.
        rules_inserted: List of rule IDs that were inserted.
        repo_dir: Path to the generated repo directory.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.
        run_id: Optional pre-created run ID from the orchestrator.
            When provided, skips creating a new run record.

    Returns:
        The run ID for the build operation.
    """
    skip_run_insert = run_id is not None
    if run_id is None:
        run_id = uuid.uuid4().hex
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    build_config = json.dumps(
        {
            "format_id": format_id,
            "rules_inserted": rules_inserted,
            "repo_dir": repo_dir,
        }
    )
    build_name = f"build-{format_id}-{len(rules_inserted)}-rules"

    with get_connection(db_path) as conn:
        if skip_run_insert:
            conn.execute(
                "UPDATE runs SET name = ?, config = ? WHERE id = ?",
                (build_name, build_config, run_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO runs
                    (id, module, name, target_id, parent_run_id,
                     config, status, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "cxp",
                    build_name,
                    None,
                    None,
                    build_config,
                    int(RunStatus.COMPLETED),
                    now_iso,
                    now_iso,
                ),
            )

    return run_id


def persist_test_result(
    result_id: str,
    campaign_id: str,
    technique_id: str,
    assistant: str,
    validation_result: str,
    db_path: Path | None = None,
) -> str | None:
    """Persist a CXP test result and create a finding on hit.

    If validation_result is "hit", creates a finding in the core findings
    table linked to the campaign run.

    Args:
        result_id: The test result UUID.
        campaign_id: The campaign/run ID.
        technique_id: The technique tested.
        assistant: The assistant tested.
        validation_result: "hit", "miss", "partial", or "pending".
        db_path: Path to database file. Defaults to ~/.qai/qai.db.

    Returns:
        The finding ID if a finding was created, None otherwise.
    """
    if validation_result != "hit":
        return None

    finding_id = uuid.uuid4().hex
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO findings
                (id, run_id, module, category, severity, title,
                 description, source_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                campaign_id,
                "cxp",
                "context-poisoning",
                int(Severity.HIGH),
                f"CXP hit: {technique_id} on {assistant}",
                f"Technique {technique_id} successfully poisoned {assistant}",
                result_id,
                now_iso,
            ),
        )

    return finding_id
