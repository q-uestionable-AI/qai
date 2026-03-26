"""Finding service — query logic for findings and associated evidence."""

from __future__ import annotations

import sqlite3

from q_ai.core.db import list_evidence as _db_list_evidence
from q_ai.core.db import list_findings as _db_list_findings
from q_ai.core.db import list_runs as _db_list_runs
from q_ai.core.models import Evidence, Finding, Severity


def list_findings(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
    module: str | None = None,
    category: str | None = None,
    min_severity: Severity | None = None,
    target_id: str | None = None,
) -> list[Finding]:
    """List findings with optional filters.

    Args:
        conn: Active database connection.
        run_id: Filter by a single run ID.
        module: Filter by module name.
        category: Filter by finding category.
        min_severity: Filter findings at or above this severity.
        target_id: Filter by target ID (joins on runs table).

    Returns:
        List of Finding objects ordered by severity DESC, created_at DESC.
    """
    return _db_list_findings(
        conn,
        run_id=run_id,
        module=module,
        category=category,
        min_severity=min_severity,
        target_id=target_id,
    )


def get_finding(
    conn: sqlite3.Connection,
    finding_id: str,
) -> tuple[Finding, list[Evidence]] | None:
    """Get a single finding by ID with its associated evidence.

    Args:
        conn: Active database connection.
        finding_id: The finding ID to look up.

    Returns:
        Tuple of (Finding, evidence list), or None if not found.
    """
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if row is None:
        return None
    finding = Finding.from_row(dict(row))
    evidence = _db_list_evidence(conn, finding_id=finding_id)
    return finding, evidence


def get_findings_for_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[Finding]:
    """Get all findings for a workflow run, including child run findings.

    Args:
        conn: Active database connection.
        run_id: The parent run ID.

    Returns:
        List of Finding objects for the run and all its child runs,
        ordered by severity DESC, created_at DESC.
    """
    child_runs = _db_list_runs(conn, parent_run_id=run_id)
    all_ids = [run_id] + [c.id for c in child_runs]
    return _db_list_findings(conn, run_ids=all_ids)
