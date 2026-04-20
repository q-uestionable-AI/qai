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
from q_ai.core.models import Finding, Run, RunStatus, Severity, Target
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
                f" token_valid, via_tunnel, timestamp, body"
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


# ---------------------------------------------------------------------------
# Per-target overview (Intel target-list and target-detail pages)
# ---------------------------------------------------------------------------


_PROBE_MODULE = "ipi-probe"
_SWEEP_MODULE = "ipi-sweep"
_IMPORT_MODULE = "import"
_OVERVIEW_MODULES = (_PROBE_MODULE, _SWEEP_MODULE, _IMPORT_MODULE)


@dataclass(slots=True)
class TargetOverviewRow:
    """Per-target summary of the most recent evidence by module.

    Attributes:
        target: The target this row describes.
        latest_probe_finished_at: ``finished_at`` of the most recent
            completed ``ipi-probe`` run for this target, or ``None``.
        latest_sweep_finished_at: ``finished_at`` of the most recent
            completed ``ipi-sweep`` run for this target, or ``None``.
        latest_import_finished_at: ``finished_at`` of the most recent
            completed ``import`` run for this target, or ``None``.
    """

    target: Target
    latest_probe_finished_at: _dt.datetime | None = None
    latest_sweep_finished_at: _dt.datetime | None = None
    latest_import_finished_at: _dt.datetime | None = None


@dataclass(slots=True)
class TargetsOverviewResult:
    """Typed result for the Intel target-list view."""

    rows: list[TargetOverviewRow]


def _as_utc(value: _dt.datetime) -> _dt.datetime:
    """Coerce a datetime to UTC-aware.

    Naive datetimes are assumed to already be UTC (matches how
    :func:`q_ai.core.db._now_iso` writes timestamps).

    Args:
        value: Datetime to coerce.

    Returns:
        UTC-aware datetime.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.UTC)
    return value.astimezone(_dt.UTC)


def _load_latest_per_target_module(
    conn: sqlite3.Connection,
    target_ids: list[str],
) -> dict[tuple[str, str], _dt.datetime]:
    """Return a ``(target_id, module) -> latest finished_at`` map.

    Runs a single GROUP BY query over completed ``ipi-probe`` /
    ``ipi-sweep`` / ``import`` runs for the supplied targets.

    Args:
        conn: Active database connection.
        target_ids: Target IDs to include. Empty list returns ``{}``.

    Returns:
        Dict keyed by ``(target_id, module)``; only populated for the
        (target, module) pairs that have at least one completed run.
    """
    if not target_ids:
        return {}

    target_ph = ", ".join("?" for _ in target_ids)
    module_ph = ", ".join("?" for _ in _OVERVIEW_MODULES)
    rows = conn.execute(
        f"SELECT target_id, module, MAX(finished_at) AS latest "  # noqa: S608
        f"FROM runs "
        f"WHERE target_id IN ({target_ph}) "
        f"AND module IN ({module_ph}) "
        f"AND status = ? "
        f"AND finished_at IS NOT NULL "
        f"GROUP BY target_id, module",
        (*target_ids, *_OVERVIEW_MODULES, int(RunStatus.COMPLETED)),
    ).fetchall()

    result: dict[tuple[str, str], _dt.datetime] = {}
    for row in rows:
        latest = row["latest"]
        if not latest:
            continue
        parsed = _dt.datetime.fromisoformat(latest)
        result[(row["target_id"], row["module"])] = _as_utc(parsed)
    return result


def _build_overview_row(
    target: Target,
    latest_map: dict[tuple[str, str], _dt.datetime],
) -> TargetOverviewRow:
    """Assemble a :class:`TargetOverviewRow` by looking up per-module latests."""
    return TargetOverviewRow(
        target=target,
        latest_probe_finished_at=latest_map.get((target.id, _PROBE_MODULE)),
        latest_sweep_finished_at=latest_map.get((target.id, _SWEEP_MODULE)),
        latest_import_finished_at=latest_map.get((target.id, _IMPORT_MODULE)),
    )


def query_targets_overview(
    conn: sqlite3.Connection,
) -> TargetsOverviewResult:
    """Load one overview row per target in a single DB pass.

    The row list is driven by :func:`list_targets`, so a target with no
    completed runs still produces a row with all three
    ``latest_*_finished_at`` fields set to ``None``. The per-module
    latests are fetched in a single ``GROUP BY`` query to avoid an N+1.
    Rows preserve the ``list_targets`` ordering (name ascending, NOCASE).
    Only ``status = COMPLETED`` runs populate the latest fields.
    Runs from other modules are ignored.
    """
    targets = _db_list_targets(conn)
    if not targets:
        return TargetsOverviewResult(rows=[])

    target_ids = [t.id for t in targets]
    latest_map = _load_latest_per_target_module(conn, target_ids)
    rows = [_build_overview_row(t, latest_map) for t in targets]
    return TargetsOverviewResult(rows=rows)


def query_target_overview_by_id(
    conn: sqlite3.Connection,
    target_id: str,
) -> TargetOverviewRow | None:
    """Load a single target's overview row.

    Args:
        conn: Active database connection.
        target_id: The target ID to look up.

    Returns:
        A populated :class:`TargetOverviewRow` or ``None`` if the target
        does not exist.
    """
    target = _db_get_target(conn, target_id)
    if target is None:
        return None
    latest_map = _load_latest_per_target_module(conn, [target_id])
    return _build_overview_row(target, latest_map)


# ---------------------------------------------------------------------------
# Age-badge formatting (Intel target list and detail pages)
# ---------------------------------------------------------------------------


_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 60 * 60
_SECONDS_PER_DAY = 24 * _SECONDS_PER_HOUR
_EM_DASH = "\u2014"


def format_age(
    dt: _dt.datetime | None,
    now: _dt.datetime | None = None,
) -> str:
    """Format a datetime as a compact age badge.

    Used for the Intel target-list evidence cells and the detail-page
    section headers. Returns the em-dash placeholder for missing data to
    match the ``targets_table.html`` convention.

    Args:
        dt: The reference timestamp, or ``None`` if no evidence exists.
        now: Optional pinned "now" for deterministic formatting in tests.
            Defaults to :func:`datetime.datetime.now` in UTC.

    Returns:
        ``"—"`` when ``dt`` is ``None``, ``"now"`` for sub-minute and
        future deltas (clock skew), ``"{N}m"`` under one hour,
        ``"{N}h"`` under one day, or ``"{N}d"`` otherwise.
    """
    if dt is None:
        return _EM_DASH

    current = _as_utc(now) if now is not None else _dt.datetime.now(_dt.UTC)
    delta = (current - _as_utc(dt)).total_seconds()

    if delta < _SECONDS_PER_MINUTE:
        return "now"
    if delta < _SECONDS_PER_HOUR:
        return f"{int(delta // _SECONDS_PER_MINUTE)}m"
    if delta < _SECONDS_PER_DAY:
        return f"{int(delta // _SECONDS_PER_HOUR)}h"
    return f"{int(delta // _SECONDS_PER_DAY)}d"


# ---------------------------------------------------------------------------
# Sweep run summaries (Intel target-detail Sweep Runs section)
# ---------------------------------------------------------------------------


_SWEEP_METADATA_EVIDENCE_TYPE = "ipi_sweep_metadata"


@dataclass(slots=True)
class SweepRunSummary:
    """Per-row summary for the Intel detail page's Sweep Runs section.

    Attributes:
        run_id: The sweep run's ID.
        status: Run status (used to render a completion badge).
        finished_at: ``finished_at`` timestamp or ``None`` if the run has
            not completed.
        template_count: Number of distinct templates measured in the
            sweep. ``0`` when the metadata blob is unavailable.
        style_count: Number of distinct styles measured in the sweep.
            ``0`` when the metadata blob is unavailable.
        reps: Repetitions per (template, style) combination, inferred as
            ``total_cases // (template_count * style_count)``. ``None``
            when the divisor is zero or the metadata blob is unavailable.
        total_cases: Total (case, rep) pairs executed. ``0`` when the
            metadata blob is unavailable.
        metadata_available: ``True`` when the ``ipi_sweep_metadata``
            evidence blob was found and parsed. ``False`` for degenerate
            DB states where the row must still render but aggregate
            fields are meaningless — templates key the "metadata
            unavailable" note off this flag, not off sentinel values.
        citation_frame: Frame recorded by the run (``"plain"`` or
            ``"template-aware"``). Defaults to ``"template-aware"``
            regardless of ``metadata_available`` — pre-v0.10.2 runs
            predate the field and the template-aware path was the only
            behavior that existed, so defaulting preserves that
            semantic for legacy data. The default is also applied when
            the metadata blob is present but missing the
            ``citation_frame`` key.
    """

    run_id: str
    status: RunStatus
    finished_at: _dt.datetime | None
    template_count: int = 0
    style_count: int = 0
    reps: int | None = None
    total_cases: int = 0
    metadata_available: bool = False
    citation_frame: str = "template-aware"


def extract_sweep_run_summary(
    conn: sqlite3.Connection,
    run: Run,
) -> SweepRunSummary:
    """Build a :class:`SweepRunSummary` for a sweep run.

    Reads the ``ipi_sweep_metadata`` evidence blob written by
    :func:`q_ai.ipi.sweep_service.persist_sweep_run` for the numeric
    aggregate fields. When the blob is missing or malformed, returns a
    summary with ``metadata_available=False`` and zeroed counts so that
    the caller can still render a row.

    Reps is inferred from ``total_cases // (template_count * style_count)``
    because ``persist_sweep_run`` does not persist ``reps`` independently.

    Args:
        conn: Active database connection.
        run: The sweep ``Run`` whose summary should be extracted. Callers
            pass the ``Run`` object from ``list_runs`` rather than a bare
            ``run_id`` so ``status`` and ``finished_at`` are not re-fetched.

    Returns:
        A populated :class:`SweepRunSummary`. Never returns ``None``;
        the ``metadata_available`` flag pins absence of the blob.
    """
    blob = evidence_service.load_evidence_json(conn, run.id, _SWEEP_METADATA_EVIDENCE_TYPE)
    if blob is None:
        # Explicit citation_frame assignment here (rather than relying on
        # the dataclass default) makes the legacy-read contract visible
        # at the construction site. Pre-v0.10.2 runs with no evidence
        # row still render the Frame column as "template-aware".
        return SweepRunSummary(
            run_id=run.id,
            status=run.status,
            finished_at=run.finished_at,
            metadata_available=False,
            citation_frame="template-aware",
        )

    total_cases_raw = blob.get("total_cases", 0)
    template_summary = blob.get("template_summary") or {}
    style_summary = blob.get("style_summary") or {}

    try:
        total_cases = int(total_cases_raw)
    except (TypeError, ValueError):
        total_cases = 0

    template_count = len(template_summary) if isinstance(template_summary, dict) else 0
    style_count = len(style_summary) if isinstance(style_summary, dict) else 0

    divisor = template_count * style_count
    reps = total_cases // divisor if divisor > 0 else None

    frame_raw = blob.get("citation_frame")
    citation_frame = frame_raw if isinstance(frame_raw, str) else "template-aware"

    return SweepRunSummary(
        run_id=run.id,
        status=run.status,
        finished_at=run.finished_at,
        template_count=template_count,
        style_count=style_count,
        reps=reps,
        total_cases=total_cases,
        metadata_available=True,
        citation_frame=citation_frame,
    )


def query_target_sweep_runs(
    conn: sqlite3.Connection,
    target_id: str,
) -> list[SweepRunSummary]:
    """List sweep-run summaries for a target, most recent first.

    The result list is driven by ``module='ipi-sweep'`` runs for the
    given target. Each row's aggregate fields come from the per-run
    ``ipi_sweep_metadata`` evidence blob — see
    :func:`extract_sweep_run_summary`. Runs missing the blob still
    render, with ``metadata_available=False``.

    Ordering is ``finished_at`` descending. Runs whose ``finished_at``
    is ``None`` (e.g. still running or failed before finish) sort last.

    Args:
        conn: Active database connection.
        target_id: The target ID whose sweep runs should be listed.

    Returns:
        List of :class:`SweepRunSummary`; empty if the target has no
        sweep runs.
    """
    runs = _db_list_runs(conn, module=_SWEEP_MODULE, target_id=target_id)
    # list_runs orders by started_at DESC; re-sort by finished_at DESC
    # with None-finishes pushed to the end so the "latest" row always
    # reflects the most recent completion. _as_utc mirrors PR #126's
    # sweep-run sort defense against mixed tz-naive/aware rows.
    runs.sort(
        key=lambda r: (
            r.finished_at is None,
            -_as_utc(r.finished_at).timestamp() if r.finished_at else 0.0,
        ),
    )
    return [extract_sweep_run_summary(conn, r) for r in runs]


# ---------------------------------------------------------------------------
# Probe run summaries (Intel target-detail Probe Runs section)
# ---------------------------------------------------------------------------


_PROBE_METADATA_EVIDENCE_TYPE = "ipi_probe_metadata"
_PROBE_MODULE = "ipi-probe"


@dataclass(slots=True)
class ProbeRunSummary:
    """Per-row summary for the Intel detail page's Probe Runs section.

    Attributes:
        run_id: The probe run's ID.
        status: Run status (used to render a completion badge).
        finished_at: ``finished_at`` timestamp or ``None`` if the run has
            not completed.
        total_probes: Total probes executed in the run. ``0`` when the
            metadata blob is unavailable or malformed.
        total_complied: Total probes where the model complied. ``0`` when
            the metadata blob is unavailable or malformed.
        overall_compliance_rate: Run-wide compliance rate (0.0-1.0).
            ``0.0`` when the metadata blob is unavailable or malformed.
        overall_severity: Derived severity for the run. Defaults to
            :attr:`Severity.INFO` when the metadata blob is unavailable
            or the serialized name does not map to a known member.
        category_count: Number of distinct probe categories measured.
            ``0`` when the metadata blob is unavailable or the
            ``category_summary`` field is not a mapping.
        metadata_available: ``True`` when the ``ipi_probe_metadata``
            evidence blob was found and every required field parsed
            cleanly. ``False`` for blob-missing, malformed-blob,
            missing-key, unknown-severity, and type-unexpected paths
            (stricter than sweep — the probe row has no meaningful
            render without severity and category count, so the flag
            doubles as a "render the muted row" switch).
    """

    run_id: str
    status: RunStatus
    finished_at: _dt.datetime | None
    total_probes: int = 0
    total_complied: int = 0
    overall_compliance_rate: float = 0.0
    overall_severity: Severity = Severity.INFO
    category_count: int = 0
    metadata_available: bool = False


def _unavailable_probe_summary(run: Run) -> ProbeRunSummary:
    """Return a :class:`ProbeRunSummary` with ``metadata_available=False``.

    Used by every defensive branch of :func:`extract_probe_run_summary`
    so the shape of the "no aggregates" summary is defined in one place.
    """
    return ProbeRunSummary(
        run_id=run.id,
        status=run.status,
        finished_at=run.finished_at,
        metadata_available=False,
    )


def extract_probe_run_summary(
    conn: sqlite3.Connection,
    run: Run,
) -> ProbeRunSummary:
    """Build a :class:`ProbeRunSummary` for a probe run.

    Reads the ``ipi_probe_metadata`` evidence blob written by
    :func:`q_ai.ipi.probe_service.persist_probe_run`. Per the Phase 3
    brief, the extractor treats missing evidence rows, malformed blobs,
    missing required keys, unknown severity names, and type-unexpected
    ``category_summary`` values as indistinguishable: all clear the
    ``metadata_available`` flag and leave numeric fields at their
    defaults. This is stricter than the sweep extractor — the probe
    row has no meaningful aggregate rendering without severity and
    category count, so a single flag suffices for the template.

    The severity value in the blob is the enum's ``name`` attribute
    (e.g. ``"HIGH"``) as written by ``persist_probe_run``; parsing
    uses ``Severity[name]``.

    Args:
        conn: Active database connection.
        run: The probe ``Run`` whose summary should be extracted. Callers
            pass the ``Run`` object from ``list_runs`` rather than a bare
            ``run_id`` so ``status`` and ``finished_at`` are not re-fetched
            (PR #128 sweep-extractor contract).

    Returns:
        A populated :class:`ProbeRunSummary`. Never raises.
    """
    blob = evidence_service.load_evidence_json(conn, run.id, _PROBE_METADATA_EVIDENCE_TYPE)
    if not isinstance(blob, dict):
        return _unavailable_probe_summary(run)

    category_summary = blob.get("category_summary")
    if not isinstance(category_summary, dict):
        return _unavailable_probe_summary(run)

    severity_name = blob.get("overall_severity")
    if not isinstance(severity_name, str):
        return _unavailable_probe_summary(run)
    try:
        severity = Severity[severity_name]
    except KeyError:
        return _unavailable_probe_summary(run)

    try:
        total_probes = int(blob.get("total_probes", 0))
    except (TypeError, ValueError):
        total_probes = 0
    try:
        total_complied = int(blob.get("total_complied", 0))
    except (TypeError, ValueError):
        total_complied = 0
    try:
        overall_rate = float(blob.get("overall_compliance_rate", 0.0))
    except (TypeError, ValueError):
        overall_rate = 0.0

    return ProbeRunSummary(
        run_id=run.id,
        status=run.status,
        finished_at=run.finished_at,
        total_probes=total_probes,
        total_complied=total_complied,
        overall_compliance_rate=overall_rate,
        overall_severity=severity,
        category_count=len(category_summary),
        metadata_available=True,
    )


# ---------------------------------------------------------------------------
# Import run summaries (Intel target-detail Imports section)
# ---------------------------------------------------------------------------


_IMPORT_MODULE = "import"


@dataclass(slots=True)
class ImportRunSummary:
    """Per-row summary for the Intel detail page's Imports section.

    Attributes:
        run_id: The import run's ID.
        status: Run status (used to render a completion badge).
        started_at: ``started_at`` timestamp (used for ordering and age
            display). May be ``None`` for runs that failed to record one.
        finished_at: ``finished_at`` timestamp or ``None`` if the run has
            not completed.
        source: Source format / tool name (e.g. ``garak``, ``pyrit``,
            ``sarif``), copied from the run's ``source`` column.
        finding_count: Number of findings persisted by this import run.
    """

    run_id: str
    status: RunStatus
    started_at: _dt.datetime | None
    finished_at: _dt.datetime | None
    source: str | None
    finding_count: int = 0


def query_target_import_runs(
    conn: sqlite3.Connection,
    target_id: str,
) -> list[ImportRunSummary]:
    """List import-run summaries for a target, most recent first.

    Driven by ``module='import'`` runs for the given target. Finding
    counts are gathered per-run via :func:`get_finding_count_for_runs`;
    the N+1 query pattern is acceptable at expected scales (tens of
    imports per target) — see the Phase 5 brief Risk #6.

    Ordering is ``started_at`` descending. Runs whose ``started_at`` is
    ``None`` sort last. ``_as_utc`` guards against mixed tz-naive and
    tz-aware rows (PR #126 precedent).

    Args:
        conn: Active database connection.
        target_id: The target ID whose import runs should be listed.

    Returns:
        List of :class:`ImportRunSummary`; empty if the target has no
        import runs.
    """
    runs = _db_list_runs(conn, module=_IMPORT_MODULE, target_id=target_id)
    runs.sort(
        key=lambda r: (
            r.started_at is None,
            -_as_utc(r.started_at).timestamp() if r.started_at else 0.0,
        ),
    )
    summaries: list[ImportRunSummary] = []
    for run in runs:
        count = get_finding_count_for_runs(conn, [run.id])
        summaries.append(
            ImportRunSummary(
                run_id=run.id,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                source=run.source,
                finding_count=count,
            )
        )
    return summaries


def query_target_probe_runs(
    conn: sqlite3.Connection,
    target_id: str,
) -> list[ProbeRunSummary]:
    """List probe-run summaries for a target, most recent first.

    The result list is driven by ``module='ipi-probe'`` runs for the
    given target. Each row's aggregate fields come from the per-run
    ``ipi_probe_metadata`` evidence blob — see
    :func:`extract_probe_run_summary`. Runs missing the blob (or with
    a malformed one) still render, with ``metadata_available=False``.

    Ordering is ``finished_at`` descending. Runs whose ``finished_at``
    is ``None`` sort last. ``_as_utc`` guards against mixed tz-naive
    and tz-aware rows (PR #126 precedent).

    Args:
        conn: Active database connection.
        target_id: The target ID whose probe runs should be listed.

    Returns:
        List of :class:`ProbeRunSummary`; empty if the target has no
        probe runs.
    """
    runs = _db_list_runs(conn, module=_PROBE_MODULE, target_id=target_id)
    runs.sort(
        key=lambda r: (
            r.finished_at is None,
            -_as_utc(r.finished_at).timestamp() if r.finished_at else 0.0,
        ),
    )
    return [extract_probe_run_summary(conn, r) for r in runs]
