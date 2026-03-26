"""Evidence service — query logic for evidence records."""

from __future__ import annotations

import sqlite3

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
