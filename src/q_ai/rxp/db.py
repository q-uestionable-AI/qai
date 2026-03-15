"""SQLite CRUD operations for RXP validation results.

All data is persisted to the unified q-ai database via q_ai.core.db.get_connection().
Table rxp_validations is created in schema V8.

Typical usage:
    >>> from q_ai.rxp.db import save_validation, get_validation
    >>> save_validation(run_id, result, profile_id, top_k, db_path=db_path)
    >>> retrieved = get_validation(validation_id, db_path=db_path)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.rxp.models import ValidationResult


def save_validation(
    run_id: str,
    result: ValidationResult,
    profile_id: str | None,
    top_k: int,
    db_path: Path | None = None,
) -> str:
    """Insert a validation result into rxp_validations.

    Args:
        run_id: The run ID this validation belongs to.
        result: ValidationResult from the validation engine.
        profile_id: Domain profile ID used, or None for custom corpus.
        top_k: Number of retrieval results per query.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        The generated validation ID.
    """
    validation_id = uuid.uuid4().hex
    results_json = json.dumps(result.to_dict())

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO rxp_validations (
                id, run_id, model_id, profile_id,
                total_queries, poison_retrievals, retrieval_rate,
                mean_poison_rank, top_k, results_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                validation_id,
                run_id,
                result.model_id,
                profile_id,
                result.total_queries,
                result.poison_retrievals,
                result.retrieval_rate,
                result.mean_poison_rank,
                top_k,
                results_json,
            ),
        )

    return validation_id


def get_validation(validation_id: str, db_path: Path | None = None) -> dict | None:
    """Retrieve a single validation by ID.

    Args:
        validation_id: The validation ID to look up.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        Dict of validation row data if found, None otherwise.
    """
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM rxp_validations WHERE id = ?", (validation_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None


def list_validations(
    model_id: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """List validations, optionally filtered by model.

    Args:
        model_id: If provided, only return validations for this model.
        db_path: Path to the SQLite database file. Defaults to ~/.qai/qai.db.

    Returns:
        List of validation row dicts ordered by created_at DESC.
    """
    with get_connection(db_path) as conn:
        if model_id is not None:
            rows = conn.execute(
                "SELECT * FROM rxp_validations WHERE model_id = ? ORDER BY created_at DESC",
                (model_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rxp_validations ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]
