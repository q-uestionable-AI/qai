"""Run service — query logic for runs and their children."""

from __future__ import annotations

import datetime as _dt
import sqlite3

from q_ai.core.db import get_run as _db_get_run
from q_ai.core.db import list_runs as _db_list_runs
from q_ai.core.models import Run, RunStatus

_TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.PARTIAL,
}


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


def conclude_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> str:
    """Conclude a campaign run by transitioning it to COMPLETED.

    Performs an atomic conditional update: only transitions rows whose
    status is not already terminal. Children of the run that are still in
    WAITING_FOR_USER are swept to COMPLETED in the same unit of work.

    Args:
        conn: Active database connection.
        run_id: The parent run ID to conclude.

    Returns:
        ``"not_found"`` if the run does not exist, ``"already_terminal"``
        if it is already in a terminal status, or ``"concluded"`` on a
        successful transition.
    """
    terminal_ints = tuple(int(s) for s in _TERMINAL_STATUSES)
    now = _dt.datetime.now(_dt.UTC).isoformat()

    run = _db_get_run(conn, run_id)
    if run is None:
        return "not_found"
    if run.status in _TERMINAL_STATUSES:
        return "already_terminal"

    non_terminal_ph = ", ".join("?" for _ in terminal_ints)
    cur = conn.execute(
        f"UPDATE runs SET status = ?, finished_at = ? "  # noqa: S608
        f"WHERE id = ? AND status NOT IN ({non_terminal_ph})",
        (int(RunStatus.COMPLETED), now, run_id, *terminal_ints),
    )
    if cur.rowcount == 0:
        # Race: the run was deleted or transitioned between the pre-check
        # above and this UPDATE. Surface as not_found so callers return 404.
        return "not_found"
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = ? WHERE parent_run_id = ? AND status = ?",
        (int(RunStatus.COMPLETED), now, run_id, int(RunStatus.WAITING_FOR_USER)),
    )
    return "concluded"


def conclude_stranded(
    conn: sqlite3.Connection,
    run_id: str,
) -> str:
    """Transition a stranded ``WAITING_FOR_USER`` run to ``CANCELLED``.

    Args:
        conn: Active database connection.
        run_id: The run ID to conclude.

    Returns:
        ``"not_found"`` if the run does not exist, ``"not_stranded"`` if
        its current status is not ``WAITING_FOR_USER``, or ``"cancelled"``
        on a successful transition.
    """
    now = _dt.datetime.now(_dt.UTC).isoformat()
    run = _db_get_run(conn, run_id)
    if run is None:
        return "not_found"
    if run.status != RunStatus.WAITING_FOR_USER:
        return "not_stranded"
    cur = conn.execute(
        "UPDATE runs SET status = ?, finished_at = ? WHERE id = ? AND status = ?",
        (int(RunStatus.CANCELLED), now, run_id, int(RunStatus.WAITING_FOR_USER)),
    )
    if cur.rowcount == 0:
        return "not_stranded"
    return "cancelled"
