"""Evidence service — query logic for evidence records."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Any

from q_ai.core.db import list_evidence as _db_list_evidence
from q_ai.core.models import Evidence


def list_evidence(
    conn: sqlite3.Connection,
    *,
    finding_id: str | None = None,
    run_id: str | None = None,
) -> list[Evidence]:
    """List evidence records with optional filters.

    Args:
        conn: Active database connection.
        finding_id: Filter by associated finding ID.
        run_id: Filter by associated run ID.

    Returns:
        List of Evidence objects ordered by created_at descending.
    """
    return _db_list_evidence(conn, finding_id=finding_id, run_id=run_id)


def get_evidence(
    conn: sqlite3.Connection,
    evidence_id: str,
) -> Evidence | None:
    """Get a single evidence record by ID.

    Args:
        conn: Active database connection.
        evidence_id: The evidence ID to look up.

    Returns:
        An Evidence instance or None if not found.
    """
    row = conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
    if row is None:
        return None
    return Evidence.from_row(dict(row))


def load_evidence_json(
    conn: sqlite3.Connection,
    run_id: str,
    evidence_type: str,
) -> dict[str, Any] | None:
    """Load and parse a single JSON evidence record by type.

    Args:
        conn: Active database connection.
        run_id: Run ID to query evidence for.
        evidence_type: Evidence type string to filter by.

    Returns:
        Parsed dict or None if not found or malformed.
    """
    row = conn.execute(
        "SELECT content FROM evidence WHERE run_id = ? AND type = ? LIMIT 1",
        (run_id, evidence_type),
    ).fetchone()
    if not row or not row["content"]:
        return None
    with contextlib.suppress(ValueError, TypeError):
        return json.loads(row["content"])  # type: ignore[no-any-return]
    return None
