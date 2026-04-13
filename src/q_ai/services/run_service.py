"""Run service — query logic for runs and their children."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from q_ai.core.db import (
    get_previously_seen_finding_keys,
    get_prior_run_counts_by_target,
)
from q_ai.core.db import get_run as _db_get_run
from q_ai.core.db import get_target as _db_get_target
from q_ai.core.db import list_runs as _db_list_runs
from q_ai.core.db import list_targets as _db_list_targets
from q_ai.core.models import Finding, Run, RunStatus, Target
from q_ai.services import audit_service, evidence_service, finding_service

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


# ---------------------------------------------------------------------------
# History and detail queries used by the runs view
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HistoryRow:
    """Enriched run data for the history table."""

    id: str
    display_name: str
    target_name: str | None
    target_id: str | None
    status: RunStatus
    finding_count: int
    duration: str
    started_at: _dt.datetime | None
    report_run_id: str | None = None
    source: str | None = None


@dataclass(slots=True)
class HistoryQueryResult:
    """Typed result for history-view queries."""

    history_runs: list[HistoryRow]
    targets: list[Target]
    prior_run_counts: dict[str, int]


@dataclass(slots=True)
class RunDetail:
    """Typed result for single-run detail queries."""

    workflow_run: Run
    child_runs: list[Run]
    findings: list[Finding]
    child_by_module: dict[str, Run]
    target: Target | None
    previously_seen: set[tuple[str, str]]
    report_run_id: str | None
    module_data: dict[str, Any] = field(default_factory=dict)


def compute_duration(run: Run) -> str:
    """Compute a human-readable duration from a run's timestamps."""
    if not run.started_at:
        return ""
    end = run.finished_at or _dt.datetime.now(_dt.UTC)
    total_s = int((end - run.started_at).total_seconds())
    mins, secs = divmod(total_s, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _effective_target_id(run: Run) -> str | None:
    """Return the run's target_id, falling back to config['target_id']."""
    return run.target_id or (run.config or {}).get("target_id")


def load_module_data(
    conn: sqlite3.Connection,
    child_by_module: dict[str, Run],
    findings: list[Finding],
) -> dict[str, Any]:
    """Load module-specific DB data for audit, inject, proxy, and IPI children.

    Filesystem-backed concerns (payload template lookup, mitigation section
    labelling) are intentionally left to the caller.

    Args:
        conn: Active database connection.
        child_by_module: Mapping of module name -> child Run.
        findings: All findings for the parent run.

    Returns:
        Dict with DB-driven module data: audit_scan, audit_findings,
        audit_evidence_map, inject_results_data, coverage_report,
        proxy_session, ipi_campaigns, ipi_hits, retrieval_gate.
    """
    audit_scan, audit_findings, audit_evidence_map = audit_service.get_audit_run_detail(
        conn, child_by_module.get("audit"), findings
    )

    inject_results_data: list[dict[str, Any]] = []
    coverage_report: dict[str, Any] | None = None
    inject_child = child_by_module.get("inject")
    if inject_child:
        rows = conn.execute(
            """SELECT id, payload_name, technique, outcome,
                      target_agent, evidence, created_at
               FROM inject_results WHERE run_id = ?
               ORDER BY created_at""",
            (inject_child.id,),
        ).fetchall()
        inject_results_data = [dict(r) for r in rows]

        coverage_report = evidence_service.load_evidence_json(
            conn, inject_child.id, "coverage_report"
        )

    proxy_session: dict[str, Any] | None = None
    proxy_child = child_by_module.get("proxy")
    if proxy_child:
        row = conn.execute(
            "SELECT * FROM proxy_sessions WHERE run_id = ? LIMIT 1",
            (proxy_child.id,),
        ).fetchone()
        proxy_session = dict(row) if row else None

    ipi_campaigns: list[dict[str, Any]] = []
    ipi_hits: list[dict[str, Any]] = []
    retrieval_gate: dict[str, Any] | None = None
    ipi_child = child_by_module.get("ipi")
    if ipi_child:
        camp_rows = conn.execute(
            """SELECT id, uuid, token, filename, format, technique,
                      callback_url, payload_style, payload_type, created_at
               FROM ipi_payloads WHERE run_id = ?
               ORDER BY created_at""",
            (ipi_child.id,),
        ).fetchall()
        ipi_campaigns = [dict(r) for r in camp_rows]

        if ipi_campaigns:
            camp_uuids = [c["uuid"] for c in ipi_campaigns]
            ph = ", ".join("?" for _ in camp_uuids)
            hit_rows = conn.execute(
                f"SELECT id, uuid, source_ip, user_agent, confidence,"  # noqa: S608
                f" token_valid, timestamp, body"
                f" FROM ipi_hits WHERE uuid IN ({ph})"
                " ORDER BY timestamp DESC",
                camp_uuids,
            ).fetchall()
            ipi_hits = [dict(r) for r in hit_rows]

        retrieval_gate = evidence_service.load_evidence_json(conn, ipi_child.id, "retrieval_gate")

    return {
        "audit_scan": audit_scan,
        "audit_findings": audit_findings,
        "audit_evidence_map": audit_evidence_map,
        "inject_results_data": inject_results_data,
        "coverage_report": coverage_report,
        "proxy_session": proxy_session,
        "ipi_campaigns": ipi_campaigns,
        "ipi_hits": ipi_hits,
        "retrieval_gate": retrieval_gate,
    }


def query_run_detail(
    conn: sqlite3.Connection,
    run_id: str,
) -> RunDetail | None:
    """Load the DB-side data for a single-run view.

    Returns ``None`` if the run does not exist. Leaves filesystem
    concerns (report markdown, artifact paths) and template context
    assembly to the caller.

    Args:
        conn: Active database connection.
        run_id: The run ID to load.

    Returns:
        A populated :class:`RunDetail` or ``None`` if the run is missing.
    """
    workflow_run, child_runs = get_run_with_children(conn, run_id)
    if workflow_run is None:
        return None

    findings = finding_service.get_findings_for_run(conn, run_id)
    child_by_module = {c.module: c for c in child_runs}

    module_data = load_module_data(conn, child_by_module, findings)

    eff_target_id = _effective_target_id(workflow_run)
    target = _db_get_target(conn, eff_target_id) if eff_target_id else None

    previously_seen: set[tuple[str, str]] = set()
    if eff_target_id and workflow_run.started_at:
        previously_seen = get_previously_seen_finding_keys(
            conn,
            eff_target_id,
            workflow_run.started_at.isoformat(),
            run_id,
        )

    report_run_id: str | None = None
    if eff_target_id:
        report_row = conn.execute(
            """SELECT id FROM runs
               WHERE name = 'generate_report' AND target_id = ?
               AND status IN (?, ?)
               ORDER BY finished_at DESC LIMIT 1""",
            (eff_target_id, int(RunStatus.COMPLETED), int(RunStatus.PARTIAL)),
        ).fetchone()
        if report_row:
            report_run_id = report_row["id"]

    return RunDetail(
        workflow_run=workflow_run,
        child_runs=child_runs,
        findings=findings,
        child_by_module=child_by_module,
        target=target,
        previously_seen=previously_seen,
        report_run_id=report_run_id,
        module_data=module_data,
    )


def query_history_runs(
    conn: sqlite3.Connection,
    *,
    workflow_filter: str | None,
    target_filter: str | None,
    status: RunStatus | None,
    resolve_workflow_display_name: Callable[[str | None], str],
    resolve_import_display_name: Callable[[str | None], str],
) -> HistoryQueryResult:
    """Load data for the run-history view.

    The two display-name resolver callbacks keep presentation concerns
    (quick-action name maps, orchestrator registry lookups) in the route
    layer while still producing fully-populated :class:`HistoryRow`
    instances in a single DB pass.

    Args:
        conn: Active database connection.
        workflow_filter: Optional workflow name filter applied to parent runs.
        target_filter: Optional target id filter.
        status: Optional run status filter.
        resolve_workflow_display_name: Callback mapping a workflow run name
            to its user-facing display name.
        resolve_import_display_name: Callback mapping an import run's
            ``source`` field to its user-facing display name.

    Returns:
        :class:`HistoryQueryResult` with sorted history rows, the target
        catalogue needed by the filter UI, and prior-run counts for each
        target on the page.
    """
    parent_runs = _db_list_runs(
        conn,
        module="workflow",
        name=workflow_filter or None,
        status=status,
        target_id=target_filter or None,
    )

    import_runs = _db_list_runs(
        conn,
        module="import",
        status=status,
        target_id=target_filter or None,
    )

    targets = _db_list_targets(conn)
    target_map = {t.id: t for t in targets}

    run_ids = [r.id for r in parent_runs] + [r.id for r in import_runs]
    finding_counts: dict[str, int] = {}
    if run_ids:
        ph = ", ".join("?" for _ in run_ids)
        rows = conn.execute(
            f"SELECT COALESCE(r.parent_run_id, r.id) as pid, COUNT(f.id) as cnt "  # noqa: S608
            f"FROM findings f JOIN runs r ON f.run_id = r.id "
            f"WHERE COALESCE(r.parent_run_id, r.id) IN ({ph}) GROUP BY pid",
            run_ids,
        ).fetchall()
        finding_counts = {r["pid"]: r["cnt"] for r in rows}

    target_ids = list(
        {_effective_target_id(r) for r in parent_runs if _effective_target_id(r) is not None}
    )
    report_runs: dict[str, str] = {}
    if target_ids:
        ph = ", ".join("?" for _ in target_ids)
        rows = conn.execute(
            f"SELECT target_id, id FROM runs "  # noqa: S608
            f"WHERE name = 'generate_report' AND target_id IN ({ph}) "
            f"AND status IN (?, ?) ORDER BY finished_at DESC",
            (*target_ids, int(RunStatus.COMPLETED), int(RunStatus.PARTIAL)),
        ).fetchall()
        for r in rows:
            if r["target_id"] not in report_runs:
                report_runs[r["target_id"]] = r["id"]

    history_runs: list[HistoryRow] = []
    for run in parent_runs:
        eff_target_id = _effective_target_id(run)
        target = target_map.get(eff_target_id) if eff_target_id else None
        history_runs.append(
            HistoryRow(
                id=run.id,
                display_name=resolve_workflow_display_name(run.name),
                target_name=target.name if target else None,
                target_id=eff_target_id,
                status=run.status,
                finding_count=finding_counts.get(run.id, 0),
                duration=compute_duration(run),
                started_at=run.started_at,
                report_run_id=report_runs.get(eff_target_id) if eff_target_id else None,
            )
        )

    for run in import_runs:
        if workflow_filter:
            continue  # Import runs don't match workflow filters
        source_name = run.source or "Unknown"
        eff_target_id = run.target_id
        target = target_map.get(eff_target_id) if eff_target_id else None
        history_runs.append(
            HistoryRow(
                id=run.id,
                display_name=resolve_import_display_name(source_name),
                target_name=target.name if target else None,
                target_id=eff_target_id,
                status=run.status,
                finding_count=finding_counts.get(run.id, 0),
                duration=compute_duration(run),
                started_at=run.started_at,
                source=source_name,
            )
        )

    history_runs.sort(
        key=lambda r: r.started_at or _dt.datetime.min.replace(tzinfo=_dt.UTC),
        reverse=True,
    )

    target_ids_on_page = [r.target_id for r in history_runs if r.target_id]
    prior_run_counts = (
        get_prior_run_counts_by_target(conn, target_ids_on_page) if target_ids_on_page else {}
    )

    return HistoryQueryResult(
        history_runs=history_runs,
        targets=targets,
        prior_run_counts=prior_run_counts,
    )
