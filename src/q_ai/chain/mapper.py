"""Mapper from ChainResult to core DB for persistence.

Bridges the chain execution domain to the core persistence domain
so chain executions are stored in the unified SQLite database.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus


def persist_chain(
    result: object,
    chain: object,
    db_path: Path | None = None,
    run_id: str | None = None,
) -> str:
    """Persist a chain execution result to the database.

    Creates a run, chain_executions row, and chain_step_outputs rows.
    Returns the run ID.

    Args:
        result: Completed ChainResult from the executor.
        chain: ChainDefinition that was executed.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.
        run_id: Optional pre-created run ID from the orchestrator.
            When provided, skips creating a new run record.

    Returns:
        The run ID for the persisted chain execution.
    """
    skip_run_insert = run_id is not None
    if run_id is None:
        run_id = uuid.uuid4().hex
    execution_id = uuid.uuid4().hex
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    chain_id = getattr(chain, "id", "")
    chain_name = getattr(chain, "name", "")
    dry_run = getattr(result, "dry_run", True)
    success = getattr(result, "success", False)
    trust_boundaries = getattr(result, "trust_boundaries_crossed", [])
    target_config = getattr(result, "target_config", {})
    step_outputs = getattr(result, "step_outputs", [])
    started_at = getattr(result, "started_at", None)
    finished_at = getattr(result, "finished_at", None)

    with get_connection(db_path) as conn:
        # Create run record only when no pre-created run_id was supplied
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
                    "chain",
                    chain_name,
                    None,
                    None,
                    None,
                    int(RunStatus.COMPLETED),
                    started_at.isoformat() if started_at else now_iso,
                    finished_at.isoformat() if finished_at else now_iso,
                ),
            )

        # Create chain_executions row
        conn.execute(
            """
            INSERT INTO chain_executions
                (id, run_id, chain_id, chain_name, dry_run,
                 template_path, target_config, success,
                 trust_boundaries, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                run_id,
                chain_id,
                chain_name,
                1 if dry_run else 0,
                None,
                json.dumps(target_config),
                1 if success else 0,
                json.dumps(trust_boundaries),
                now_iso,
            ),
        )

        # Create chain_step_outputs rows
        for step_output in step_outputs:
            step_output_id = uuid.uuid4().hex
            so_step_id = getattr(step_output, "step_id", "")
            so_module = getattr(step_output, "module", "")
            so_technique = getattr(step_output, "technique", "")
            so_success = getattr(step_output, "success", False)
            so_status = str(getattr(step_output, "status", ""))
            so_artifacts = getattr(step_output, "artifacts", {})
            so_error = getattr(step_output, "error", None)
            so_started_at = getattr(step_output, "started_at", None)
            so_finished_at = getattr(step_output, "finished_at", None)

            conn.execute(
                """
                INSERT INTO chain_step_outputs
                    (id, execution_id, step_id, module, technique,
                     success, status, artifacts, error,
                     started_at, finished_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_output_id,
                    execution_id,
                    so_step_id,
                    so_module,
                    so_technique,
                    1 if so_success else 0,
                    so_status,
                    json.dumps(so_artifacts),
                    so_error,
                    so_started_at.isoformat() if so_started_at else None,
                    so_finished_at.isoformat() if so_finished_at else None,
                    now_iso,
                ),
            )

    return run_id
