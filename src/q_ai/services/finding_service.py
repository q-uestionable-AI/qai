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


def get_imported_findings_for_target(
    conn: sqlite3.Connection,
    target_id: str,
    exclude_run_ids: list[str] | None = None,
) -> list[Finding]:
    """Get findings from import runs associated with a target.

    Queries findings where the parent run has ``module='import'`` and
    ``target_id`` matches. Optionally excludes findings from specific
    run IDs to avoid double-counting with native workflow findings.

    Args:
        conn: Active database connection.
        target_id: Target ID to scope the query.
        exclude_run_ids: Run IDs to exclude from results.

    Returns:
        List of Finding objects ordered by severity DESC, created_at DESC.
    """
    query = (
        "SELECT f.* FROM findings f "
        "JOIN runs r ON f.run_id = r.id "
        "WHERE r.target_id = ? AND r.module = 'import'"
    )
    params: list[object] = [target_id]

    if exclude_run_ids:
        placeholders = ", ".join("?" for _ in exclude_run_ids)
        query += f" AND f.run_id NOT IN ({placeholders})"
        params.extend(exclude_run_ids)

    query += " ORDER BY f.severity DESC, f.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [Finding.from_row(dict(row)) for row in rows]
