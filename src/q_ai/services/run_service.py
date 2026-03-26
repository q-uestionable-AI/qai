"""Run service — query logic for runs and their children."""

from __future__ import annotations

import sqlite3

from q_ai.core.db import get_run as _db_get_run
from q_ai.core.db import list_runs as _db_list_runs
from q_ai.core.models import Run, RunStatus


def get_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> Run | None:
    """Get a single run by ID.

    Args:
        conn: Active database connection.
        run_id: The run ID to look up.

    Returns:
        A Run instance or None if not found.
    """
    return _db_get_run(conn, run_id)


def list_runs(
    conn: sqlite3.Connection,
    *,
    module: str | None = None,
    status: RunStatus | None = None,
    target_id: str | None = None,
    parent_run_id: str | None = None,
    name: str | None = None,
) -> list[Run]:
    """List runs with optional filters.

    Args:
        conn: Active database connection.
        module: Filter by module name.
        status: Filter by run status.
        target_id: Filter by target ID.
        parent_run_id: Filter by parent run ID.
        name: Filter by run name (workflow ID for parent runs).

    Returns:
        List of Run objects ordered by started_at descending.
    """
    return _db_list_runs(
        conn,
        module=module,
        status=status,
        target_id=target_id,
        parent_run_id=parent_run_id,
        name=name,
    )


def get_child_runs(
    conn: sqlite3.Connection,
    parent_run_id: str,
) -> list[Run]:
    """Get all child runs for a parent run.

    Args:
        conn: Active database connection.
        parent_run_id: The parent run ID.

    Returns:
        List of child Run objects ordered by started_at descending.
    """
    return _db_list_runs(conn, parent_run_id=parent_run_id)


def get_run_with_children(
    conn: sqlite3.Connection,
    run_id: str,
) -> tuple[Run | None, list[Run]]:
    """Get a run and its child runs in one call.

    Args:
        conn: Active database connection.
        run_id: The parent run ID.

    Returns:
        Tuple of (parent Run or None, list of child Runs).
    """
    parent = _db_get_run(conn, run_id)
    if parent is None:
        return None, []
    children = _db_list_runs(conn, parent_run_id=run_id)
    return parent, children


def get_finding_count_for_runs(
    conn: sqlite3.Connection,
    run_ids: list[str],
) -> int:
    """Count findings across a set of run IDs.

    Args:
        conn: Active database connection.
        run_ids: Run IDs to count findings for.

    Returns:
        Total finding count. Returns 0 for empty run_ids.
    """
    if not run_ids:
        return 0
    ph = ", ".join("?" for _ in run_ids)
    row = conn.execute(
        f"SELECT COUNT(*) FROM findings WHERE run_id IN ({ph})",  # noqa: S608
        run_ids,
    ).fetchone()
    count: int = row[0]
    return count


def get_child_run_ids(
    conn: sqlite3.Connection,
    parent_run_id: str,
) -> list[str]:
    """Get child run IDs without loading full Run objects.

    Args:
        conn: Active database connection.
        parent_run_id: The parent run ID.

    Returns:
        List of child run ID strings.
    """
    rows = conn.execute("SELECT id FROM runs WHERE parent_run_id = ?", (parent_run_id,)).fetchall()
    return [r["id"] for r in rows]
