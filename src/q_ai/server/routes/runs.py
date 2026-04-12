"""Runs, findings, and export route handlers."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json as _json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from q_ai.audit.reporting.csv_report import generate_csv_report
from q_ai.audit.reporting.ndjson_report import generate_ndjson_report
from q_ai.core.db import (
    delete_run_cascade,
    export_run_bundle,
    get_connection,
    get_previously_seen_finding_keys,
    get_prior_run_counts_by_target,
    get_setting,
    get_target,
    list_targets,
)
from q_ai.core.guidance import RunGuidance
from q_ai.core.mitigation import SourceType
from q_ai.core.models import RunStatus, Severity
from q_ai.orchestrator.registry import get_workflow, list_workflows
from q_ai.server.routes._shared import (
    _QUICK_ACTION_DISPLAY_NAMES,
    _TERMINAL_STATUSES,
    _get_db_path,
    _get_templates,
    logger,
)
from q_ai.services import audit_service, evidence_service, finding_service, run_service

router = APIRouter()

_STATUS_NAMES = [s.name for s in RunStatus]

_SEV_MAP = {4: "Critical", 3: "High", 2: "Medium", 1: "Low", 0: "Info"}

_ARTIFACTS_BASE = Path.home() / ".qai" / "artifacts"

_MAX_BULK_RUNS = 50


def _compute_duration(run: Any) -> str:
    """Compute human-readable duration from a run's timestamps."""
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


def _count_findings_by_severity(findings: list[Any]) -> dict[str, int]:
    """Count findings grouped by severity label, omitting zeros."""
    counts: dict[str, int] = dict.fromkeys(["Critical", "High", "Medium", "Low", "Info"], 0)
    for f in findings:
        label = _SEV_MAP.get(f.severity.value, "Info")
        counts[label] += 1
    return {k: v for k, v in counts.items() if v > 0}


def _mitigation_section_label(section: Any) -> str:
    """User-facing label for a mitigation GuidanceSection."""
    st = getattr(section, "source_type", None)
    if st == SourceType.TAXONOMY:
        ids = ", ".join(section.source_ids) if section.source_ids else ""
        return (
            f"Recommended by OWASP MCP Top 10 ({ids})" if ids else "Recommended by OWASP MCP Top 10"
        )
    if st == SourceType.RULE:
        return "Recommended based on finding characteristics"
    return "Considerations for your environment"


def _load_module_data(
    conn: Any,
    child_by_module: dict[str, Any],
    findings: list[Any],
) -> dict[str, Any]:
    """Load module-specific data for audit, inject, and proxy child runs.

    Returns:
        Dict with keys: audit_scan, audit_findings, audit_evidence_map,
        inject_results_data, payload_template_map, proxy_session.
    """
    audit_scan, audit_findings, audit_evidence_map = audit_service.get_audit_run_detail(
        conn, child_by_module.get("audit"), findings
    )
    inject_results_data: list[dict[str, Any]] = []
    coverage_report: dict[str, Any] | None = None
    proxy_session: dict[str, Any] | None = None

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

    # Build payload template lookup for inject results drill-down
    payload_template_map: dict[str, dict[str, Any]] = {}
    if inject_results_data:
        from q_ai.inject.payloads.loader import load_all_templates as _load_inject_templates

        for tmpl in _load_inject_templates():
            payload_template_map[tmpl.name] = {
                "tool_description": tmpl.tool_description,
                "test_query": tmpl.test_query or f"Use the {tmpl.tool_name} tool.",
            }

    proxy_child = child_by_module.get("proxy")
    if proxy_child:
        row = conn.execute(
            "SELECT * FROM proxy_sessions WHERE run_id = ? LIMIT 1",
            (proxy_child.id,),
        ).fetchone()
        proxy_session = dict(row) if row else None

    # IPI data: campaigns, hits, and retrieval gate for the IPI child run
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
        "payload_template_map": payload_template_map,
        "proxy_session": proxy_session,
        "mitigation_section_label": _mitigation_section_label,
        "ipi_campaigns": ipi_campaigns,
        "ipi_hits": ipi_hits,
        "retrieval_gate": retrieval_gate,
    }


def _build_runs_context(db_path: Path | None, run_id: str) -> dict[str, Any]:
    """Load all data for the runs results view (blocking, run off event loop)."""
    with get_connection(db_path) as conn:
        workflow_run, child_runs = run_service.get_run_with_children(conn, run_id)
        if not workflow_run:
            return {"previously_seen": set()}
        findings = finding_service.get_findings_for_run(conn, run_id)
        child_by_module = {c.module: c for c in child_runs}

        workflow = get_workflow(workflow_run.name) if workflow_run.name else None
        wf_name = (
            workflow.name
            if workflow
            else _QUICK_ACTION_DISPLAY_NAMES.get(
                workflow_run.name or "", workflow_run.name or "Workflow"
            )
        )
        wf_modules = list(workflow.modules) if workflow else []

        module_data = _load_module_data(conn, child_by_module, findings)

        # Deserialize RunGuidance from child runs for playbook rendering
        child_guidance: dict[str, RunGuidance | None] = {}
        for mod, child in child_by_module.items():
            if child.guidance:
                with contextlib.suppress(TypeError, ValueError):
                    child_guidance[mod] = RunGuidance.from_dict(_json.loads(child.guidance))
            else:
                child_guidance[mod] = None
        module_data["child_guidance"] = child_guidance

        eff_target_id = workflow_run.target_id or (workflow_run.config or {}).get("target_id")
        target = None
        if eff_target_id:
            target = get_target(conn, eff_target_id)

        previously_seen: set[tuple[str, str]] = set()
        if eff_target_id and workflow_run.started_at:
            previously_seen = get_previously_seen_finding_keys(
                conn,
                eff_target_id,
                workflow_run.started_at.isoformat(),
                run_id,
            )

        # Look for existing generate_report run for this target
        report_run_id = None
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

        # For generate_report runs: render report markdown as HTML
        report_html = ""
        is_report_run = workflow_run.name == "generate_report"
        has_evidence_zip = False
        if is_report_run:
            report_html, has_evidence_zip = _load_report_html(run_id)
            # For report runs, the run itself is the report_run_id
            report_run_id = run_id

        result: dict[str, Any] = {
            "workflow_run": workflow_run,
            "child_runs": child_runs,
            "findings": findings,
            "results_mode": True,
            "is_terminal": workflow_run.status in _TERMINAL_STATUSES,
            "workflow_display_name": wf_name,
            "duration_display": _compute_duration(workflow_run),
            "finding_counts": _count_findings_by_severity(findings),
            "workflow_modules": wf_modules,
            "child_by_module": child_by_module,
            "target": target,
            "report_run_id": report_run_id,
            "report_html": report_html,
            "is_report_run": is_report_run,
            "has_evidence_zip": has_evidence_zip,
            "previously_seen": previously_seen,
        }
        result.update(module_data)
        result["has_audit_findings"] = bool(module_data.get("audit_findings"))
        return result


class _HistoryRow:
    """Enriched run data for the history table template."""

    __slots__ = (
        "display_name",
        "duration",
        "finding_count",
        "id",
        "report_run_id",
        "source",
        "started_at",
        "status",
        "target_id",
        "target_name",
    )

    def __init__(
        self,
        *,
        run_id: str,
        display_name: str,
        target_name: str | None,
        target_id: str | None,
        status: RunStatus,
        finding_count: int,
        duration: str,
        started_at: _dt.datetime | None,
        report_run_id: str | None = None,
        source: str | None = None,
    ) -> None:
        self.id = run_id
        self.display_name = display_name
        self.target_name = target_name
        self.target_id = target_id
        self.status = status
        self.finding_count = finding_count
        self.duration = duration
        self.started_at = started_at
        self.report_run_id = report_run_id
        self.source = source


def _collect_stranded_runs(request: Request) -> list[dict[str, Any]]:
    """Return stranded-run display rows for the run-history banner.

    Reads ``app.state.stranded_runs`` (captured at server startup) and
    filters out any run that has an active ``WorkflowRunner`` in
    ``app.state.active_workflows``. Such a run is legitimately waiting,
    not stranded.

    Args:
        request: The incoming FastAPI request carrying app state.

    Returns:
        A list of dicts with ``run_id``, ``display_name``, and
        ``started_at`` keys, sorted by ``started_at`` descending.
    """
    stranded: dict[str, tuple[str | None, _dt.datetime | None]] = getattr(
        request.app.state, "stranded_runs", {}
    )
    active: dict[str, object] = getattr(request.app.state, "active_workflows", {})
    entries: list[tuple[str, str | None, _dt.datetime | None]] = [
        (rid, name, started_at) for rid, (name, started_at) in stranded.items() if rid not in active
    ]
    entries.sort(
        key=lambda e: e[2] or _dt.datetime.min.replace(tzinfo=_dt.UTC),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for rid, name, started_at in entries:
        wf = get_workflow(name) if name else None
        display_name = (
            wf.name if wf else _QUICK_ACTION_DISPLAY_NAMES.get(name or "", name or "Workflow")
        )
        started_str = started_at.strftime("%Y-%m-%d %H:%M") if started_at else None
        rows.append({"run_id": rid, "display_name": display_name, "started_at": started_str})
    return rows


def _build_history_context(
    db_path: Path | None,
    workflow_filter: str | None,
    target_filter: str | None,
    status_filter: str | None,
    group_by_target: bool = False,
) -> dict[str, Any]:
    """Load context for the run history view (blocking, run off event loop)."""
    parsed_status = _parse_status(status_filter)
    with get_connection(db_path) as conn:
        parent_runs = run_service.list_runs(
            conn,
            module="workflow",
            name=workflow_filter or None,
            status=parsed_status,
            target_id=target_filter or None,
        )

        # Include import runs alongside workflow runs
        import_runs = run_service.list_runs(
            conn,
            module="import",
            status=parsed_status,
            target_id=target_filter or None,
        )

        targets = list_targets(conn)
        target_map = {t.id: t for t in targets}

        # Batch finding counts and report runs to fix N+1 queries (O(1) instead of O(N))
        run_ids = [r.id for r in parent_runs] + [r.id for r in import_runs]
        finding_counts = {}
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
            {
                r.target_id or (r.config or {}).get("target_id")
                for r in parent_runs
                if r.target_id or (r.config or {}).get("target_id")
            }
        )
        report_runs = {}
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

        history_runs: list[_HistoryRow] = []
        for run in parent_runs:
            finding_count = finding_counts.get(run.id, 0)

            wf = get_workflow(run.name) if run.name else None
            display_name = (
                wf.name
                if wf
                else _QUICK_ACTION_DISPLAY_NAMES.get(run.name or "", run.name or "Workflow")
            )

            eff_target_id = run.target_id or (run.config or {}).get("target_id")
            target = target_map.get(eff_target_id) if eff_target_id else None
            target_name = target.name if target else None

            duration = _compute_duration(run)

            # Get pre-computed latest report run
            report_run_id = report_runs.get(eff_target_id)

            history_runs.append(
                _HistoryRow(
                    run_id=run.id,
                    display_name=display_name,
                    target_name=target_name,
                    target_id=eff_target_id,
                    status=run.status,
                    finding_count=finding_count,
                    duration=duration,
                    started_at=run.started_at,
                    report_run_id=report_run_id,
                )
            )

        for run in import_runs:
            if workflow_filter:
                continue  # Import runs don't match workflow filters
            source_name = run.source or "Unknown"
            display_name = f"Import ({source_name.title()})"
            finding_count = finding_counts.get(run.id, 0)

            eff_target_id = run.target_id
            target = target_map.get(eff_target_id) if eff_target_id else None
            target_name = target.name if target else None

            history_runs.append(
                _HistoryRow(
                    run_id=run.id,
                    display_name=display_name,
                    target_name=target_name,
                    target_id=eff_target_id,
                    status=run.status,
                    finding_count=finding_count,
                    duration=_compute_duration(run),
                    started_at=run.started_at,
                    source=source_name,
                )
            )

        # Re-sort by started_at descending after merging
        history_runs.sort(
            key=lambda r: r.started_at or _dt.datetime.min.replace(tzinfo=_dt.UTC),
            reverse=True,
        )

        target_ids_on_page = [r.target_id for r in history_runs if r.target_id]
        prior_run_counts = (
            get_prior_run_counts_by_target(conn, target_ids_on_page) if target_ids_on_page else {}
        )

    return {
        "history_runs": history_runs,
        "workflows": list_workflows(),
        "targets": targets,
        "statuses": _STATUS_NAMES,
        "current_workflow": workflow_filter or "",
        "current_target": target_filter or "",
        "current_status": status_filter or "",
        "group_by_target": group_by_target,
        "prior_run_counts": prior_run_counts,
    }


@router.get("/runs")
async def runs(
    request: Request,
    run_id: str | None = Query(None),
    workflow: str | None = Query(None),
    target_id: str | None = Query(None),
    status: str | None = Query(None),
    group_by_target: str | None = Query(None),
) -> HTMLResponse:
    """Render the runs view — history list or single-run results."""
    from q_ai.server.routes.assist import _get_suggested_prompts

    templates = _get_templates(request)
    db_path = _get_db_path(request)

    ctx: dict[str, Any] = {
        "active": "runs",
        "run_id": run_id,
        "workflow_run": None,
        "child_runs": [],
        "findings": [],
        "scan_status": None,
        "campaign_status": None,
        "results_mode": False,
        "is_terminal": False,
        "workflow_display_name": "",
        "target": None,
        "duration_display": "",
        "finding_counts": {},
        "workflow_modules": [],
        "child_by_module": {},
        "audit_scan": None,
        "audit_findings": [],
        "audit_evidence_map": {},
        "inject_results_data": [],
        "payload_template_map": {},
        "proxy_session": None,
        "report_run_id": None,
        "report_html": "",
        "is_report_run": False,
        "has_evidence_zip": False,
    }

    if run_id:
        run_ctx = await asyncio.to_thread(_build_runs_context, db_path, run_id)
        ctx.update(run_ctx)
    else:
        do_group = group_by_target in ("1", "true", "on")
        history_ctx = await asyncio.to_thread(
            _build_history_context, db_path, workflow, target_id, status, do_group
        )
        ctx.update(history_ctx)
        ctx["stranded_runs"] = _collect_stranded_runs(request)

    # Assist panel context for run results mode
    if ctx.get("results_mode"):
        with get_connection(db_path) as conn:
            assist_provider = get_setting(conn, "assist.provider") or ""
            assist_model = get_setting(conn, "assist.model") or ""
            modules = list(ctx.get("child_by_module", {}).keys())
            ctx["assist_configured"] = bool(assist_provider and assist_model)
            ctx["assist_prompts"] = _get_suggested_prompts(
                conn, page="run_results", modules=modules
            )

    return templates.TemplateResponse(request, "runs.html", ctx)


def _parse_status(value: str | None) -> RunStatus | None:
    """Safely parse a status filter value."""
    if value is None:
        return None
    try:
        return RunStatus[value.upper()]
    except (KeyError, AttributeError):
        try:
            return RunStatus(int(value))
        except (ValueError, KeyError):
            return None


def _parse_severity(value: str | None) -> Severity | None:
    """Safely parse a severity filter value."""
    if value is None:
        return None
    try:
        return Severity[value.upper()]
    except (KeyError, AttributeError):
        try:
            return Severity(int(value))
        except (ValueError, KeyError):
            return None


def _sync_list_runs(
    db_path: Path | None,
    module: str | None,
    status: RunStatus | None,
    target_id: str | None,
) -> list:
    """Load runs list (blocking, run off event loop)."""
    with get_connection(db_path) as conn:
        return run_service.list_runs(conn, module=module, status=status, target_id=target_id)


@router.get("/api/runs")
async def api_runs(
    request: Request,
    module: str | None = Query(None),
    status: str | None = Query(None),
    target_id: str | None = Query(None),
) -> HTMLResponse:
    """Return the runs table partial for HTMX swap."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    parsed_status = _parse_status(status)
    runs_list = await asyncio.to_thread(
        _sync_list_runs, db_path, module or None, parsed_status, target_id or None
    )
    return templates.TemplateResponse(request, "partials/runs_table.html", {"runs": runs_list})


@router.get("/api/runs/history")
async def api_runs_history(
    request: Request,
    workflow: str | None = Query(None),
    target_id: str | None = Query(None),
    status: str | None = Query(None),
    group_by_target: str | None = Query(None),
) -> HTMLResponse:
    """Return the run history table partial for HTMX swap."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    do_group = group_by_target in ("1", "true", "on")
    history_ctx = await asyncio.to_thread(
        _build_history_context, db_path, workflow, target_id, status, do_group
    )
    return templates.TemplateResponse(request, "partials/run_history_table.html", history_ctx)


def _sync_export_run(db_path: Path | None, run_id: str) -> dict | None:
    """Load and export a run bundle (blocking, run off event loop)."""
    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
        if run is None:
            return None
        return export_run_bundle(conn, run_id)


@router.get("/api/runs/{run_id}/export", response_model=None)
async def api_export_run(
    request: Request,
    run_id: str,
    fmt: str = Query("json", alias="format"),
) -> Response:
    """Export a run as JSON bundle, NDJSON, or CSV."""
    if fmt not in ("json", "ndjson", "csv"):
        return JSONResponse(status_code=400, content={"detail": f"Unknown format: {fmt}"})

    db_path = _get_db_path(request)

    if fmt == "json":
        bundle = await asyncio.to_thread(_sync_export_run, db_path, run_id)
        if bundle is None:
            return JSONResponse(status_code=404, content={"detail": "Run not found"})
        return JSONResponse(
            content=bundle,
            headers={
                "Content-Disposition": (f'attachment; filename="run-{run_id[:12]}.json"'),
            },
        )

    # NDJSON / CSV: load findings from DB and generate
    def _export_format() -> bytes | None:
        import tempfile as _tempfile

        with get_connection(db_path) as conn:
            run = run_service.get_run(conn, run_id)
            if run is None:
                return None
            findings = finding_service.get_findings_for_run(conn, run_id)

            eff_target_id = run.target_id or (run.config or {}).get("target_id")
            target = get_target(conn, eff_target_id) if eff_target_id else None
            meta = {
                "run_id": run_id,
                "started_at": (run.started_at.isoformat() if run.started_at else None),
                "target_name": target.name if target else None,
            }

            with _tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            try:
                if fmt == "ndjson":
                    generate_ndjson_report(findings, tmp_path, run_metadata=meta)
                else:
                    generate_csv_report(findings, tmp_path, run_metadata=meta)

                return tmp_path.read_bytes()
            finally:
                tmp_path.unlink(missing_ok=True)

    data = await asyncio.to_thread(_export_format)
    if data is None:
        return JSONResponse(status_code=404, content={"detail": "Run not found"})

    media = "application/x-ndjson" if fmt == "ndjson" else "text/csv"
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": (f'attachment; filename="run-{run_id[:12]}.{fmt}"'),
        },
    )


def _cleanup_files(files: list[str]) -> None:
    """Delete files from disk, logging failures."""
    for file_path in files:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete file: %s", file_path)


def _sync_bulk_delete(
    db_path: Path | None,
    run_ids: list[str],
) -> tuple[int, list[str], list[str]]:
    """Delete multiple runs in a single transaction (blocking).

    Args:
        db_path: Path to the SQLite database.
        run_ids: List of run IDs to delete.

    Returns:
        Tuple of (deleted count, list of failed run IDs, files to clean up).
    """
    deleted = 0
    failed: list[str] = []
    all_files: list[str] = []
    with get_connection(db_path) as conn:
        for rid in run_ids:
            try:
                files = delete_run_cascade(conn, rid)
                all_files.extend(files)
                deleted += 1
            except ValueError:
                failed.append(rid)
    return deleted, failed, all_files


def _validate_bulk_run_ids(
    body: object,
    verb: str = "process",
) -> list[str] | JSONResponse:
    """Parse and validate a bulk run_ids payload.

    Args:
        body: The parsed JSON body (may not be a dict).
        verb: Action verb for error messages (e.g. "delete", "export").

    Returns:
        A validated list of run ID strings, or a JSONResponse on error.
    """
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request body must be a JSON object"},
        )
    run_ids = body.get("run_ids")
    if not isinstance(run_ids, list) or not run_ids:
        return JSONResponse(
            status_code=422,
            content={"detail": "run_ids must be a non-empty list"},
        )
    if not all(isinstance(rid, str) for rid in run_ids):
        return JSONResponse(
            status_code=422,
            content={"detail": "Every run_id must be a string"},
        )
    if len(run_ids) > _MAX_BULK_RUNS:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Cannot {verb} more than {_MAX_BULK_RUNS} runs at once"},
        )
    return run_ids


@router.delete("/api/runs/bulk", response_model=None)
async def api_bulk_delete_runs(request: Request) -> Response:
    """Delete multiple runs and all related data."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})

    result = _validate_bulk_run_ids(body, verb="delete")
    if isinstance(result, JSONResponse):
        return result
    run_ids: list[str] = result

    db_path = _get_db_path(request)
    deleted, failed, files_to_delete = await asyncio.to_thread(_sync_bulk_delete, db_path, run_ids)

    if files_to_delete:
        await asyncio.to_thread(_cleanup_files, files_to_delete)

    return JSONResponse(content={"deleted": deleted, "failed": failed})


def _sync_delete_run(db_path: Path | None, run_id: str) -> list[str]:
    """Delete a run cascade (blocking, run off event loop).

    Raises:
        ValueError: If run_id does not exist.
    """
    with get_connection(db_path) as conn:
        return delete_run_cascade(conn, run_id)


@router.delete("/api/runs/{run_id}", response_model=None)
async def api_delete_run(request: Request, run_id: str) -> Response:
    """Delete a run and all related data."""
    db_path = _get_db_path(request)
    try:
        files_to_delete = await asyncio.to_thread(_sync_delete_run, db_path, run_id)
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "Run not found"})

    # Clean up files after DB commit — run off event loop
    if files_to_delete:
        await asyncio.to_thread(_cleanup_files, files_to_delete)

    return JSONResponse(content={"detail": "Run deleted"})


def _sync_bulk_export(
    db_path: Path | None,
    run_ids: list[str],
) -> list[tuple[str, bytes]]:
    """Export multiple runs as JSON bundles (blocking).

    Args:
        db_path: Path to the SQLite database.
        run_ids: List of run IDs to export.

    Returns:
        List of (filename, json_bytes) tuples for ZIP assembly.
    """
    bundles: list[tuple[str, bytes]] = []
    with get_connection(db_path) as conn:
        for rid in run_ids:
            run = run_service.get_run(conn, rid)
            if run is None:
                continue
            bundle = export_run_bundle(conn, rid)
            wf_name = run.name or "workflow"
            filename = f"{rid[:12]}_{wf_name}.json"
            bundles.append((filename, _json.dumps(bundle, indent=2).encode("utf-8")))
    return bundles


def _build_zip(bundles: list[tuple[str, bytes]]) -> bytes:
    """Build a ZIP archive from named byte entries (blocking).

    Args:
        bundles: List of (filename, data) tuples to include.

    Returns:
        The complete ZIP archive as bytes.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, data in bundles:
            zf.writestr(filename, data)
    return buf.getvalue()


@router.post("/api/runs/bulk-export", response_model=None)
async def api_bulk_export_runs(request: Request) -> Response:
    """Export multiple runs as a ZIP of JSON bundles."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})

    result = _validate_bulk_run_ids(body, verb="export")
    if isinstance(result, JSONResponse):
        return result
    run_ids: list[str] = result

    db_path = _get_db_path(request)
    bundles = await asyncio.to_thread(_sync_bulk_export, db_path, run_ids)

    if not bundles:
        return JSONResponse(status_code=404, content={"detail": "No valid runs found"})

    zip_bytes = await asyncio.to_thread(_build_zip, bundles)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="runs-export.zip"',
        },
    )


def _build_compare_context(
    db_path: Path | None,
    left_id: str,
    right_id: str,
) -> dict[str, Any]:
    """Load context for the side-by-side run comparison (blocking).

    Args:
        db_path: Path to the SQLite database.
        left_id: ID of the left run.
        right_id: ID of the right run.

    Returns:
        Dict with left/right run data and finding diff.
    """
    with get_connection(db_path) as conn:
        left_run = run_service.get_run(conn, left_id)
        right_run = run_service.get_run(conn, right_id)
        if left_run is None or right_run is None:
            return {}

        left_children = run_service.get_child_runs(conn, left_id)
        right_children = run_service.get_child_runs(conn, right_id)

        left_findings = finding_service.get_findings_for_run(conn, left_id)
        right_findings = finding_service.get_findings_for_run(conn, right_id)

        left_eff_target_id = left_run.target_id or (left_run.config or {}).get("target_id")
        right_eff_target_id = right_run.target_id or (right_run.config or {}).get("target_id")
        left_target = get_target(conn, left_eff_target_id) if left_eff_target_id else None
        right_target = get_target(conn, right_eff_target_id) if right_eff_target_id else None

    left_wf = get_workflow(left_run.name) if left_run.name else None
    right_wf = get_workflow(right_run.name) if right_run.name else None

    # Diff findings by (title, module, category, severity) exact match
    def _fkey(f: Any) -> tuple[str, str, str, int]:
        return (f.title, f.module, f.category, f.severity.value)

    left_keys = {_fkey(f) for f in left_findings}
    right_keys = {_fkey(f) for f in right_findings}
    common_keys = left_keys & right_keys
    left_only = [f for f in left_findings if _fkey(f) not in right_keys]
    right_only = [f for f in right_findings if _fkey(f) not in left_keys]
    common = [f for f in left_findings if _fkey(f) in common_keys]

    # Module coverage — include child run modules and finding-level modules
    left_modules = sorted({c.module for c in left_children} | {f.module for f in left_findings})
    right_modules = sorted({c.module for c in right_children} | {f.module for f in right_findings})

    return {
        "left_run": left_run,
        "right_run": right_run,
        "left_display": left_wf.name if left_wf else (left_run.name or "Workflow"),
        "right_display": right_wf.name if right_wf else (right_run.name or "Workflow"),
        "left_target": left_target,
        "right_target": right_target,
        "left_duration": _compute_duration(left_run),
        "right_duration": _compute_duration(right_run),
        "left_only": left_only,
        "right_only": right_only,
        "common": common,
        "left_modules": left_modules,
        "right_modules": right_modules,
    }


@router.get("/runs/compare")
async def runs_compare(
    request: Request,
    left: str = Query(...),
    right: str = Query(...),
) -> HTMLResponse:
    """Render the side-by-side run comparison view."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)

    ctx = await asyncio.to_thread(_build_compare_context, db_path, left, right)
    if not ctx:
        return HTMLResponse(status_code=404, content="One or both runs not found")

    ctx["active"] = "runs"
    return templates.TemplateResponse(request, "runs_compare.html", ctx)


@router.get("/api/findings")
async def api_findings(
    request: Request,
    module: str | None = Query(None),
    category: str | None = Query(None),
    severity: str | None = Query(None),
) -> HTMLResponse:
    """Return the findings table partial for HTMX swap."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    parsed_severity = _parse_severity(severity)
    with get_connection(db_path) as conn:
        findings = finding_service.list_findings(
            conn,
            module=module or None,
            category=category or None,
            min_severity=parsed_severity,
        )
    return templates.TemplateResponse(
        request, "partials/findings_table.html", {"findings": findings}
    )


@router.get("/api/targets")
async def api_targets(request: Request) -> HTMLResponse:
    """Return the targets table partial for HTMX swap."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        targets = list_targets(conn)
    return templates.TemplateResponse(request, "partials/targets_table.html", {"targets": targets})


@router.get("/api/operations/status-bar")
async def operations_status_bar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render child run badges for the given workflow run."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    with get_connection(db_path) as conn:
        child_runs = run_service.get_child_runs(conn, run_id)
    return templates.TemplateResponse(
        request,
        "partials/child_run_badges.html",
        {"child_runs": child_runs},
    )


@router.get("/api/operations/workflow-status-bar")
async def operations_workflow_status_bar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render the full workflow status bar partial (badge, elapsed, report link)."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    with get_connection(db_path) as conn:
        workflow_run = run_service.get_run(conn, run_id)
    wf = get_workflow(workflow_run.name) if workflow_run and workflow_run.name else None
    display_name = wf.name if wf else (workflow_run.name if workflow_run else "Workflow")
    return templates.TemplateResponse(
        request,
        "partials/status_bar.html",
        {"workflow_run": workflow_run, "workflow_display_name": display_name},
    )


@router.get("/api/operations/findings-sidebar")
async def operations_findings_sidebar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render the findings sidebar for the given workflow run."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    with get_connection(db_path) as conn:
        findings = finding_service.get_findings_for_run(conn, run_id)
    return templates.TemplateResponse(
        request,
        "partials/findings_sidebar.html",
        {"findings": findings},
    )


def _get_exports_base() -> Path:
    """Return the exports base directory, resolved at call time.

    Uses ``Path.home()`` at call time so monkeypatching works in tests.
    """
    return Path.home() / ".qai" / "exports"


def _get_report_root() -> Path:
    """Return the resolved report root for path-traversal checks."""
    return (_get_exports_base() / "generate_report").resolve()


def _load_report_html(run_id: str) -> tuple[str, bool]:
    """Load and render a generate_report's report.md as sanitized HTML.

    Computes paths dynamically from ``Path.home()`` so that monkeypatching
    works in tests.

    Args:
        run_id: The generate_report run ID.

    Returns:
        Tuple of (rendered HTML string, whether evidence ZIP exists).
        Returns empty string for HTML if report file is missing.
    """
    # Deferred: only needed for report rendering, not on every request
    import markdown  # type: ignore[import-untyped]
    import nh3

    exports_base = _get_exports_base()
    report_root = _get_report_root()

    report_path = (exports_base / "generate_report" / run_id / "report.md").resolve()
    if not report_path.is_relative_to(report_root) or not report_path.is_file():
        return "", False

    md_content = report_path.read_text(encoding="utf-8")
    raw_html = markdown.markdown(
        md_content,
        extensions=["fenced_code", "tables"],
    )
    clean_html = nh3.clean(
        raw_html,
        tags={
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "br",
            "hr",
            "ul",
            "ol",
            "li",
            "strong",
            "em",
            "code",
            "pre",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "blockquote",
            "a",
        },
        attributes={"a": {"href"}, "td": {"align"}, "th": {"align"}},
    )

    zip_path = (exports_base / "generate_report" / run_id / "report.zip").resolve()
    has_zip = zip_path.is_relative_to(report_root) and zip_path.is_file()

    return clean_html, has_zip


@router.get("/api/exports/{run_id}/report", response_model=None)
def export_report(request: Request, run_id: str) -> Response:
    """Serve a generated report.md file for the given run.

    Validates that the run_id exists in the database and that the report
    file is within the expected exports directory (path traversal prevention).
    """
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"detail": "Run not found"})

    exports_base = _get_exports_base()
    report_root = _get_report_root()

    report_path = (exports_base / "generate_report" / run_id / "report.md").resolve()
    if not report_path.is_relative_to(report_root):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    if not report_path.is_file():
        return JSONResponse(status_code=404, content={"detail": "Report not found"})

    return FileResponse(
        path=report_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"report-{run_id[:12]}.md",
    )


@router.get("/api/exports/{run_id}/evidence", response_model=None)
def export_evidence(request: Request, run_id: str) -> Response:
    """Serve a generated report.zip evidence pack for the given run.

    Validates that the run_id exists in the database and that the ZIP
    file is within the expected exports directory (path traversal prevention).
    """
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"detail": "Run not found"})

    exports_base = _get_exports_base()
    report_root = _get_report_root()

    zip_path = (exports_base / "generate_report" / run_id / "report.zip").resolve()
    if not zip_path.is_relative_to(report_root):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    if not zip_path.is_file():
        return JSONResponse(status_code=404, content={"detail": "Evidence pack not found"})

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"evidence-{run_id[:12]}.zip",
    )


def _sync_generate_sarif(db_path: Path | None, run_id: str) -> bytes | None:
    """Generate a SARIF report from audit findings stored in the database.

    Reconstructs ScanFinding objects from DB Finding rows and passes them
    through the standard SARIF generator. Best-effort conversion — evidence
    and remediation are already merged into the description field during
    persistence.

    Args:
        db_path: Path to the SQLite database.
        run_id: The parent workflow run ID.

    Returns:
        SARIF JSON bytes on success, or None if no audit findings exist.
    """
    import tempfile as _tempfile
    from dataclasses import dataclass, field
    from datetime import UTC, datetime

    from q_ai.audit.reporting.sarif_report import generate_sarif_report
    from q_ai.core.mitigation import MitigationGuidance
    from q_ai.mcp.models import ScanFinding
    from q_ai.mcp.models import Severity as ScanSeverity

    severity_map = {
        4: ScanSeverity.CRITICAL,
        3: ScanSeverity.HIGH,
        2: ScanSeverity.MEDIUM,
        1: ScanSeverity.LOW,
        0: ScanSeverity.INFO,
    }

    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
        if run is None:
            return None
        child_runs = run_service.get_child_runs(conn, run_id)
        audit_child = next((c for c in child_runs if c.module == "audit"), None)
        if audit_child is None:
            return None
        findings = finding_service.list_findings(conn, run_id=audit_child.id)
        if not findings:
            return None

        # Load audit_scans metadata
        scan_row = conn.execute(
            "SELECT * FROM audit_scans WHERE run_id = ? LIMIT 1",
            (audit_child.id,),
        ).fetchone()

    scan_meta = dict(scan_row) if scan_row else {}

    # Convert DB Finding → ScanFinding
    scan_findings: list[ScanFinding] = []
    for f in findings:
        mitigation_obj = None
        if f.mitigation:
            with contextlib.suppress(TypeError, ValueError):
                mitigation_obj = MitigationGuidance.from_dict(f.mitigation)
        scan_findings.append(
            ScanFinding(
                rule_id=f.category,
                category=f.category,
                title=f.title,
                description=f.description or "",
                severity=severity_map.get(f.severity.value, ScanSeverity.INFO),
                tool_name=f.source_ref or "",
                framework_ids=f.framework_ids or {},
                timestamp=f.created_at or datetime.now(UTC),
                mitigation=mitigation_obj,
            )
        )

    # Build minimal ScanResult-like object
    @dataclass
    class _SarifData:
        findings: list[ScanFinding] = field(default_factory=list)
        server_info: dict = field(default_factory=dict)
        tools_scanned: int = 0
        scanners_run: list[str] = field(default_factory=list)
        started_at: datetime | None = None
        finished_at: datetime | None = None
        errors: list[dict] = field(default_factory=list)

    try:
        scanners_run = _json.loads(scan_meta.get("scanners_run", "[]"))
    except (ValueError, TypeError):
        logger.warning("Malformed scanners_run JSON in scan metadata; defaulting to []")
        scanners_run = []
    sarif_data = _SarifData(
        findings=scan_findings,
        server_info={
            "name": scan_meta.get("server_name", "unknown"),
            "version": scan_meta.get("server_version", "unknown"),
        },
        tools_scanned=0,
        scanners_run=scanners_run if isinstance(scanners_run, list) else [],
        started_at=audit_child.started_at,
        finished_at=audit_child.finished_at,
    )

    with _tempfile.NamedTemporaryFile(suffix=".sarif", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        generate_sarif_report(sarif_data, tmp_path)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/api/runs/{run_id}/sarif", response_model=None)
async def api_export_sarif(request: Request, run_id: str) -> Response:
    """Export audit findings as a SARIF 2.1.0 report.

    Generates SARIF from the audit child run's findings stored in the
    database. Returns 404 if no audit findings exist for the run.

    Args:
        request: The incoming FastAPI request.
        run_id: The parent workflow run ID.

    Returns:
        SARIF JSON file download, or 404 if no audit findings.
    """
    db_path = _get_db_path(request)
    data = await asyncio.to_thread(_sync_generate_sarif, db_path, run_id)
    if data is None:
        return JSONResponse(status_code=404, content={"detail": "No audit findings for this run"})
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="scan-{run_id[:12]}.sarif"',
        },
    )


def _read_proxy_messages(
    session_file: str,
    direction: str | None,
    page: int,
    per_page: int,
) -> tuple[list[dict[str, Any]], int]:
    """Read, filter, and paginate proxy messages from a session file.

    Resolves the session file path with a path-traversal guard, parses the
    JSON, optionally filters by direction, and returns a page of transformed
    message dicts together with the total filtered count.

    Args:
        session_file: Relative path stored in the proxy_sessions row.
        direction: Optional direction filter (e.g. ``"client_to_server"``).
        page: 1-based page number.
        per_page: Number of messages per page.

    Returns:
        A tuple of ``(messages, total)`` where *messages* is the current page
        and *total* is the count after filtering.
    """
    artifacts_dir = _ARTIFACTS_BASE.resolve()
    session_path = (artifacts_dir / session_file).resolve()
    if not session_path.is_relative_to(artifacts_dir):
        return [], 0
    if not session_path.is_file():
        return [], 0

    try:
        raw = _json.loads(session_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raw = {}

    all_msgs: list[dict[str, Any]] = raw.get("messages", [])
    if direction:
        all_msgs = [m for m in all_msgs if m.get("direction") == direction]

    total = len(all_msgs)
    end = page * per_page
    page_msgs = all_msgs[:end]

    messages: list[dict[str, Any]] = []
    for msg in page_msgs:
        d = msg.get("direction", "")
        arrow = "\u2192" if d == "client_to_server" else "\u2190"
        messages.append(
            {
                "sequence": msg.get("sequence"),
                "direction": arrow,
                "direction_raw": d,
                "method": msg.get("method") or "(response)",
                "timestamp": msg.get("timestamp", ""),
                "body": _json.dumps(msg.get("body", msg.get("params", {})), indent=2),
            }
        )

    return messages, total


def _fetch_proxy_messages(
    db_path: Path | None, run_id: str, direction: str | None, page: int, per_page: int
) -> tuple[list[dict[str, Any]], int]:
    """Fetch proxy messages for the given run (blocking, run off event loop)."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT session_file FROM proxy_sessions WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if not row or not row["session_file"]:
        return [], 0
    return _read_proxy_messages(row["session_file"], direction, page, per_page)


@router.get("/api/runs/proxy-messages/{run_id}")
async def api_runs_proxy_messages(
    request: Request,
    run_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    direction: str | None = Query(None),
) -> HTMLResponse:
    """Return paginated proxy messages as an HTMX partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)

    messages, total = await asyncio.to_thread(
        _fetch_proxy_messages, db_path, run_id, direction, page, per_page
    )

    has_next = (page * per_page) < total
    return templates.TemplateResponse(
        request,
        "partials/proxy_messages.html",
        {
            "messages": messages,
            "run_id": run_id,
            "page": page,
            "per_page": per_page,
            "total": total,
            "has_next": has_next,
            "direction_filter": direction,
        },
    )
