"""Route handlers for the q-ai web UI."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json as _json
import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketDisconnect

from q_ai.audit.reporting.csv_report import generate_csv_report
from q_ai.audit.reporting.ndjson_report import generate_ndjson_report
from q_ai.audit.scanner.registry import list_scanner_names
from q_ai.core.config import delete_credential, get_credential, set_credential
from q_ai.core.db import (
    create_target,
    delete_run_cascade,
    export_run_bundle,
    get_connection,
    get_previously_seen_finding_keys,
    get_prior_run_counts_by_target,
    get_setting,
    get_target,
    list_targets,
    set_setting,
)
from q_ai.core.guidance import RunGuidance
from q_ai.core.mitigation import MitigationGuidance, SourceType
from q_ai.core.models import Evidence, RunStatus, Severity
from q_ai.core.providers import (
    PROVIDERS,
    ProviderType,
    fetch_models,
    get_configured_providers,
    get_provider,
)
from q_ai.cxp.formats import list_formats as list_cxp_formats
from q_ai.inject.models import InjectionTechnique
from q_ai.orchestrator.registry import get_workflow, list_workflows
from q_ai.orchestrator.runner import WorkflowRunner
from q_ai.services import finding_service, run_service

logger = logging.getLogger(__name__)

router = APIRouter()

_STATUS_NAMES = [s.name for s in RunStatus]


def _get_templates(request: Request) -> Jinja2Templates:
    """Get the Jinja2Templates instance from app state."""
    result: Jinja2Templates = request.app.state.templates
    return result


def _get_db_path(request: Request) -> Path | None:
    """Get the database path from app state."""
    result: Path | None = request.app.state.db_path
    return result


def _detect_local_ip() -> str:
    """Detect the local network IP address for callback URL suggestion.

    Uses a UDP socket connection to determine which interface the OS
    would route to an external address. Does not send any traffic.

    Returns:
        Local IP address string, or "127.0.0.1" on failure.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            addr: str = s.getsockname()[0]
            return addr
    except OSError:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Full-page routes
# ---------------------------------------------------------------------------


@router.get("/")
async def launcher(request: Request) -> HTMLResponse:
    """Render the workflow launcher page."""
    templates = _get_templates(request)

    hero_workflow: dict[str, Any] | None = None
    workflows: list[dict[str, Any]] = []
    for wf in list_workflows():
        if not wf.visible_in_launcher:
            continue
        entry = {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "modules": wf.modules,
            "implemented": wf.executor is not None,
            "requires_provider": wf.requires_provider,
        }
        if wf.is_hero:
            hero_workflow = entry
        else:
            workflows.append(entry)

    db_path = _get_db_path(request)
    all_providers = get_configured_providers(db_path)
    providers = [p for p in all_providers if p["configured"]]

    with get_connection(db_path) as conn:
        default_transport = get_setting(conn, "audit.default_transport") or "stdio"
        saved_callback_url = get_setting(conn, "ipi.default_callback_url") or ""
        defaults = {
            "ipi_callback_url": saved_callback_url or f"http://{_detect_local_ip()}:8080/callback",
            "audit_default_transport": default_transport,
        }

    return templates.TemplateResponse(
        request,
        "launcher.html",
        {
            "active": "launcher",
            "hero_workflow": hero_workflow,
            "workflows": workflows,
            "providers": providers,
            "defaults": defaults,
            "injection_techniques": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in InjectionTechnique
            ],
            "scanner_categories": list_scanner_names(),
            "cxp_formats": list_cxp_formats(),
        },
    )


@router.get("/operations")
async def operations_redirect(request: Request) -> RedirectResponse:
    """Redirect /operations to /runs (backward compat for one release)."""
    url = "/runs"
    if request.url.query:
        url += f"?{request.url.query}"
    return RedirectResponse(url=url, status_code=301)


_TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.PARTIAL,
}

_SEV_MAP = {4: "Critical", 3: "High", 2: "Medium", 1: "Low", 0: "Info"}


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


def _load_audit_data(
    conn: Any,
    audit_child: Any,
    findings: list[Any],
) -> tuple[dict[str, Any] | None, list[Any], dict[str, list[Any]]]:
    """Load audit-specific data: scan record, findings, and evidence map.

    Args:
        conn: Active database connection.
        audit_child: The audit child run, or None.
        findings: All findings for the parent run.

    Returns:
        Tuple of (audit_scan dict or None, audit_findings list,
        audit_evidence_map keyed by finding ID).
    """
    if not audit_child:
        return None, [], {}

    row = conn.execute(
        "SELECT * FROM audit_scans WHERE run_id = ? LIMIT 1",
        (audit_child.id,),
    ).fetchone()
    audit_scan = dict(row) if row else None

    audit_findings = [f for f in findings if f.run_id == audit_child.id]
    audit_evidence_map: dict[str, list[Any]] = {}

    for af in audit_findings:
        audit_evidence_map[af.id] = []
        af.mitigation_guidance = None
        if af.mitigation:
            with contextlib.suppress(TypeError, ValueError):
                af.mitigation_guidance = MitigationGuidance.from_dict(af.mitigation)

    if audit_findings:
        finding_ids = [af.id for af in audit_findings]
        ph = ", ".join("?" for _ in finding_ids)
        ev_rows = conn.execute(
            f"SELECT * FROM evidence WHERE finding_id IN ({ph}) ORDER BY created_at DESC",  # noqa: S608
            finding_ids,
        ).fetchall()
        for ev_row in ev_rows:
            ev = Evidence.from_row(dict(ev_row))
            if ev.finding_id in audit_evidence_map:
                audit_evidence_map[ev.finding_id].append(ev)

    return audit_scan, audit_findings, audit_evidence_map


def _load_evidence_json(conn: Any, run_id: str, evidence_type: str) -> dict[str, Any] | None:
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
        return _json.loads(row["content"])  # type: ignore[no-any-return]
    return None


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
    audit_scan, audit_findings, audit_evidence_map = _load_audit_data(
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

        coverage_report = _load_evidence_json(conn, inject_child.id, "coverage_report")

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

        retrieval_gate = _load_evidence_json(conn, ipi_child.id, "retrieval_gate")

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

    return templates.TemplateResponse(request, "runs.html", ctx)


# ---------------------------------------------------------------------------
# HTMX partial routes
# ---------------------------------------------------------------------------


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


_MAX_BULK_RUNS = 50


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


# ---------------------------------------------------------------------------
# Report export route
# ---------------------------------------------------------------------------

_ARTIFACTS_BASE = Path.home() / ".qai" / "artifacts"


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


# ---------------------------------------------------------------------------
# Chain API routes
# ---------------------------------------------------------------------------


@router.get("/api/chain/tab")
async def api_chain_tab(request: Request) -> HTMLResponse:
    """Return the chain tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/chain_tab.html", {})


@router.get("/api/chain/executions")
async def api_chain_executions(request: Request) -> HTMLResponse:
    """Return chain executions list partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ce.id, ce.run_id, ce.chain_id, ce.chain_name,
                   ce.dry_run, ce.success, ce.trust_boundaries, ce.created_at
            FROM chain_executions ce
            ORDER BY ce.created_at DESC
            LIMIT 50
            """
        ).fetchall()
    executions = [dict(row) for row in rows]
    return templates.TemplateResponse(
        request, "partials/chain_tab.html", {"executions": executions}
    )


@router.get("/api/chain/executions/{run_id}")
async def api_chain_execution_detail(request: Request, run_id: str) -> HTMLResponse:
    """Return chain execution detail partial with step outputs."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        exec_row = conn.execute(
            "SELECT * FROM chain_executions WHERE run_id = ?", (run_id,)
        ).fetchone()
        step_rows = []
        if exec_row:
            step_rows = conn.execute(
                """
                SELECT step_id, module, technique, success, status, error, artifacts
                FROM chain_step_outputs
                WHERE execution_id = ?
                ORDER BY created_at
                """,
                (exec_row["id"],),
            ).fetchall()
    execution_detail: dict[str, Any] = dict(exec_row) if exec_row else {}
    step_outputs: list[dict[str, Any]] = []
    for row in step_rows:
        step = dict(row)
        # Deserialize guidance from artifacts if present
        raw_artifacts = step.get("artifacts")
        step["guidance"] = None
        if raw_artifacts:
            try:
                parsed = _json.loads(raw_artifacts) if isinstance(raw_artifacts, str) else {}
                if isinstance(parsed, dict) and "guidance" in parsed:
                    step["guidance"] = RunGuidance.from_dict(_json.loads(parsed["guidance"]))
            except (ValueError, TypeError):
                pass
        step_outputs.append(step)
    return templates.TemplateResponse(
        request,
        "partials/chain_tab.html",
        {"execution_detail": execution_detail, "step_outputs": step_outputs},
    )


# ---------------------------------------------------------------------------
# IPI API routes
# ---------------------------------------------------------------------------


@router.get("/api/ipi/tab")
async def api_ipi_tab(request: Request) -> HTMLResponse:
    """Return the IPI tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/ipi_tab.html", {})


@router.get("/api/ipi/campaigns")
async def api_ipi_campaigns(request: Request) -> HTMLResponse:
    """Return IPI campaigns summary partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ip.uuid, ip.format, ip.technique, ip.created_at,
                   COUNT(ih.id) as hit_count
            FROM ipi_payloads ip
            LEFT JOIN ipi_hits ih ON ip.uuid = ih.uuid
            GROUP BY ip.uuid, ip.format, ip.technique, ip.created_at
            ORDER BY ip.created_at DESC
            LIMIT 50
            """
        ).fetchall()
        total_hits_row = conn.execute("SELECT COUNT(*) FROM ipi_hits").fetchone()
        high_hits_row = conn.execute(
            "SELECT COUNT(*) FROM ipi_hits WHERE confidence = 'high'"
        ).fetchone()
    campaigns = [dict(row) for row in rows]
    total_hits = total_hits_row[0] if total_hits_row else 0
    high_hits = high_hits_row[0] if high_hits_row else 0
    return templates.TemplateResponse(
        request,
        "partials/ipi_tab.html",
        {
            "campaigns": campaigns,
            "total_hits": total_hits,
            "high_hits": high_hits,
            "listener_hint": True,
        },
    )


# ---------------------------------------------------------------------------
# CXP API routes
# ---------------------------------------------------------------------------


@router.get("/api/cxp/tab")
async def api_cxp_tab(request: Request) -> HTMLResponse:
    """Return the CXP tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/cxp_tab.html", {})


@router.get("/api/cxp/results")
async def api_cxp_results(request: Request) -> HTMLResponse:
    """Return CXP test results with stats."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, technique_id, assistant, model, format_id,
                   validation_result, created_at
            FROM cxp_test_results
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
        total_row = conn.execute("SELECT COUNT(*) FROM cxp_test_results").fetchone()
        hit_row = conn.execute(
            "SELECT COUNT(*) FROM cxp_test_results WHERE validation_result = 'hit'"
        ).fetchone()
        partial_row = conn.execute(
            "SELECT COUNT(*) FROM cxp_test_results WHERE validation_result = 'partial'"
        ).fetchone()
        miss_row = conn.execute(
            "SELECT COUNT(*) FROM cxp_test_results WHERE validation_result = 'miss'"
        ).fetchone()
    results = [dict(row) for row in rows]
    total_tests = total_row[0] if total_row else 0
    hit_count = hit_row[0] if hit_row else 0
    partial_count = partial_row[0] if partial_row else 0
    miss_count = miss_row[0] if miss_row else 0
    return templates.TemplateResponse(
        request,
        "partials/cxp_tab.html",
        {
            "results": results,
            "total_tests": total_tests,
            "hit_count": hit_count,
            "partial_count": partial_count,
            "miss_count": miss_count,
        },
    )


def _sync_trigger_override(db_path: Path | None, run_id: str, override_text: str) -> str:
    """Apply a trigger prompt override to a run's guidance JSON.

    Returns:
        ``"not_found"``, ``"no_guidance"``, or ``"ok"``.
    """
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT guidance FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return "not_found"
        raw = row["guidance"]
        if not raw:
            return "no_guidance"
        try:
            data = _json.loads(raw)
        except (ValueError, _json.JSONDecodeError):
            return "no_guidance"
        for block in data.get("blocks", []):
            if block.get("kind") == "trigger_prompts":
                block.setdefault("metadata", {})["override"] = override_text
                conn.execute(
                    "UPDATE runs SET guidance = ? WHERE id = ?",
                    (_json.dumps(data), run_id),
                )
                return "ok"
    return "no_guidance"


@router.post("/api/cxp/{run_id}/trigger-override")
async def api_cxp_trigger_override(request: Request, run_id: str) -> JSONResponse:
    """Persist a researcher's trigger prompt override for a CXP run.

    Updates the trigger_prompts block's ``metadata.override`` field in the
    persisted RunGuidance JSON for the given run.

    Args:
        request: The incoming FastAPI request.
        run_id: The CXP child run identifier whose guidance is updated.

    Returns:
        JSONResponse with ``{"status": "saved"}`` on success, or a 4xx
        error with ``{"detail": ...}`` on validation failure.
    """
    try:
        body = await request.json()
    except (ValueError, _json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "Expected JSON object"})

    override_text = body.get("prompt")
    if not isinstance(override_text, str) or not override_text.strip():
        return JSONResponse(status_code=400, content={"detail": "Missing or empty prompt"})

    db_path = _get_db_path(request)
    result = await asyncio.to_thread(_sync_trigger_override, db_path, run_id, override_text.strip())
    if result == "not_found":
        return JSONResponse(status_code=404, content={"detail": "Run not found"})
    if result == "no_guidance":
        return JSONResponse(status_code=400, content={"detail": "No guidance on run"})
    return JSONResponse(content={"status": "saved"})


# ---------------------------------------------------------------------------
# RXP API routes
# ---------------------------------------------------------------------------


@router.get("/api/rxp/tab")
async def api_rxp_tab(request: Request) -> HTMLResponse:
    """Return the RXP tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/rxp_tab.html", {})


@router.get("/api/rxp/validations")
async def api_rxp_validations(request: Request) -> HTMLResponse:
    """Return RXP validations with stats."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, model_id, profile_id, retrieval_rate,
                   mean_poison_rank, created_at
            FROM rxp_validations
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
        total_row = conn.execute("SELECT COUNT(*) FROM rxp_validations").fetchone()
        models_row = conn.execute("SELECT COUNT(DISTINCT model_id) FROM rxp_validations").fetchone()
        avg_row = conn.execute("SELECT AVG(retrieval_rate) FROM rxp_validations").fetchone()
    validations = [dict(row) for row in rows]
    total_validations = total_row[0] if total_row else 0
    models_tested = models_row[0] if models_row else 0
    avg_retrieval_rate = avg_row[0] if avg_row and avg_row[0] is not None else 0.0
    return templates.TemplateResponse(
        request,
        "partials/rxp_tab.html",
        {
            "validations": validations,
            "total_validations": total_validations,
            "models_tested": models_tested,
            "avg_retrieval_rate": avg_retrieval_rate,
        },
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for live workflow event updates.

    Connects through the ConnectionManager for event broadcasting.
    """
    manager = websocket.app.state.ws_manager
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def _get_providers_status(request: Request) -> list[dict[str, Any]]:
    """Build a list of provider statuses."""
    db_path = _get_db_path(request)
    result: list[dict[str, Any]] = []
    with get_connection(db_path) as conn:
        for name in PROVIDERS:
            keyring_unavailable = False
            try:
                cred = get_credential(name)
            except RuntimeError:
                cred = None
                keyring_unavailable = True
            base_url = get_setting(conn, f"{name}.base_url") or ""
            configured = cred is not None or bool(base_url)
            result.append(
                {
                    "name": name,
                    "configured": configured,
                    "has_key": cred is not None,
                    "base_url": base_url,
                    "keyring_unavailable": keyring_unavailable,
                }
            )
    return result


# ---------------------------------------------------------------------------
# Settings routes
# ---------------------------------------------------------------------------


@router.get("/settings")
async def settings_page(request: Request) -> HTMLResponse:
    """Render the settings page."""
    templates = _get_templates(request)
    providers_status = _get_providers_status(request)
    db_path = _get_db_path(request)

    with get_connection(db_path) as conn:
        defaults = {
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
        }

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "providers_status": providers_status,
            "defaults": defaults,
        },
    )


@router.get("/api/settings/providers")
async def api_list_providers(request: Request) -> JSONResponse:
    """List configured providers with status."""
    return JSONResponse(content={"providers": _get_providers_status(request)})


@router.post("/api/settings/providers")
async def api_add_provider(request: Request) -> JSONResponse:
    """Add a provider -- key to keyring, base_url to DB settings."""
    body = await request.json()
    provider = body.get("provider", "").strip().lower()
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()

    if not provider:
        return JSONResponse(
            status_code=422,
            content={"detail": "Provider name required"},
        )

    cloud_providers = {"anthropic", "openai", "groq", "openrouter"}
    if provider in cloud_providers and not api_key:
        return JSONResponse(
            status_code=422,
            content={"detail": "API key required for cloud providers"},
        )

    if api_key:
        try:
            set_credential(provider, api_key)
        except RuntimeError:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": (
                        "Keyring unavailable — set credentials via environment variable instead."
                    ),
                },
            )
        except Exception:
            logger.exception("Failed to store credential for %s", provider)
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to store credential"},
            )

    if base_url:
        db_path = _get_db_path(request)
        with get_connection(db_path) as conn:
            set_setting(conn, f"{provider}.base_url", base_url)

    return JSONResponse(
        status_code=201,
        content={"status": "ok", "provider": provider},
    )


@router.delete("/api/settings/providers/{provider}")
async def api_delete_provider(request: Request, provider: str) -> JSONResponse:
    """Delete a provider -- remove from keyring and DB."""
    provider = provider.strip().lower()
    with contextlib.suppress(Exception):
        delete_credential(provider)

    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        set_setting(conn, f"{provider}.base_url", "")

    return JSONResponse(content={"status": "deleted"})


async def _test_local_provider(db_path: Path | None, provider: str) -> JSONResponse:
    """Test connectivity for a local provider (ollama, lmstudio, custom).

    Args:
        db_path: Path to the SQLite database.
        provider: The local provider identifier.

    Returns:
        JSONResponse with connectivity status or error details.
    """
    with get_connection(db_path) as conn:
        base_url = get_setting(conn, f"{provider}.base_url")

    default_urls = {
        "ollama": "http://localhost:11434",
        "lmstudio": "http://localhost:1234",
    }
    url = base_url or default_urls.get(provider, "")
    if not url:
        return JSONResponse(
            status_code=404,
            content={"detail": "No base URL configured"},
        )

    health_path = "/api/tags" if provider == "ollama" else "/v1/models"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(f"{url}{health_path}")
            if resp.status_code == 200:
                return JSONResponse(
                    content={"status": "ok", "message": "Connected"},
                )
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"HTTP {resp.status_code}",
                },
            )
    except Exception:
        logger.exception("Provider connectivity check failed for %s", provider)
        return JSONResponse(
            content={"status": "error", "message": "Connection check failed"},
        )


@router.get("/api/settings/providers/{provider}/test")
async def api_test_provider(request: Request, provider: str) -> JSONResponse:
    """Test provider connectivity with a minimal check."""
    local_providers = {"ollama", "lmstudio", "custom"}

    if provider in local_providers:
        return await _test_local_provider(_get_db_path(request), provider)

    # Cloud provider -- check if credential exists
    try:
        credential = get_credential(provider)
    except RuntimeError:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Keyring unavailable — set credentials via environment variable instead."
                ),
            },
        )
    if credential is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Provider not configured"},
        )
    return JSONResponse(
        content={"status": "ok", "message": "Credential configured"},
    )


@router.get("/api/settings/defaults")
async def api_get_defaults(request: Request) -> JSONResponse:
    """Get default settings."""
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        defaults = {
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
            "assist.provider": (get_setting(conn, "assist.provider") or ""),
            "assist.model": (get_setting(conn, "assist.model") or ""),
        }
    return JSONResponse(content=defaults)


@router.post("/api/settings/defaults")
async def api_save_defaults(request: Request) -> JSONResponse:
    """Save default settings to DB."""
    body = await request.json()
    db_path = _get_db_path(request)
    allowed_keys = (
        "audit.default_transport",
        "ipi.default_callback_url",
        "assist.provider",
        "assist.model",
    )
    with get_connection(db_path) as conn:
        for key in allowed_keys:
            value = body.get(key)
            if value is not None:
                set_setting(conn, key, str(value))
    return JSONResponse(content={"status": "saved"})


@router.get("/api/settings/infrastructure")
async def api_infrastructure_status(request: Request) -> HTMLResponse:
    """Check local endpoint reachability, return HTML partial."""
    import httpx

    templates = _get_templates(request)
    db_path = _get_db_path(request)

    with get_connection(db_path) as conn:
        ollama_url = get_setting(conn, "ollama.base_url") or "http://localhost:11434"
        lmstudio_url = get_setting(conn, "lmstudio.base_url") or "http://localhost:1234"

    endpoints = [
        ("Ollama", ollama_url, "/api/tags"),
        ("LM Studio", lmstudio_url, "/v1/models"),
    ]
    results: list[dict[str, Any]] = []
    for name, url, health_path in endpoints:
        try:
            async with httpx.AsyncClient(timeout=3.0) as http:
                resp = await http.get(f"{url}{health_path}")
                reachable = resp.status_code == 200
        except Exception:
            reachable = False
        results.append({"name": name, "url": url, "reachable": reachable})

    return templates.TemplateResponse(
        request,
        "partials/infrastructure_content.html",
        {"infrastructure": results},
    )


@router.get("/api/providers/{name}/models")
async def api_provider_models(request: Request, name: str) -> Response:
    """Fetch models for a provider and return an HTML partial."""
    templates = _get_templates(request)
    config = get_provider(name)
    if config is None:
        return HTMLResponse(
            content="<div class='text-error text-sm'>Unknown provider</div>",
            status_code=404,
        )

    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        try:
            cred = get_credential(name)
        except RuntimeError:
            cred = None
        base_url = get_setting(conn, f"{name}.base_url") or ""

    configured = cred is not None or bool(base_url)
    if not configured and config.type != ProviderType.CUSTOM:
        return HTMLResponse(
            content=(
                "<div class='text-error text-sm'>Provider not configured. "
                "<a href='/settings#providers' class='link'>Settings</a></div>"
            ),
            status_code=400,
        )

    result = await fetch_models(name, base_url or None)

    selector_id = request.query_params.get("selector_id", "default")
    default_model_id = request.query_params.get("default", "")

    return templates.TemplateResponse(
        request,
        "partials/model_area.html",
        {
            "models": result.models,
            "supports_custom": result.supports_custom,
            "error": result.error,
            "message": result.message,
            "selector_id": selector_id,
            "provider_name": name,
            "default_model_id": default_model_id,
            "provider_type": config.type.value,
        },
    )


# ---------------------------------------------------------------------------
# Data routes (for launcher dropdowns)
# ---------------------------------------------------------------------------


@router.get("/api/targets/list")
async def api_targets_list(request: Request) -> JSONResponse:
    """Return registered targets for the launcher dropdown."""
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, type, uri FROM targets ORDER BY created_at DESC"
        ).fetchall()
    return JSONResponse(content={"targets": [dict(r) for r in rows]})


def _check_target_name_exists(db_path: Path | None, name: str) -> bool:
    """Check whether a target with the given name exists in the database.

    Args:
        db_path: Path to the SQLite database.
        name: Target name to look up.

    Returns:
        True if a target with the name exists, False otherwise.
    """
    normalized = name.strip()
    if not normalized:
        return False
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM targets WHERE name = ? LIMIT 1",
            (normalized,),
        ).fetchone()
    return row is not None


@router.get("/api/targets/check-name")
async def api_check_target_name(
    request: Request,
    name: str = Query(...),
) -> JSONResponse:
    """Check if a target with the given name already exists.

    Runs the database lookup off the event loop via ``asyncio.to_thread``
    to avoid blocking on synchronous SQLite I/O.

    Args:
        request: The incoming HTTP request.
        name: Target name to check (query parameter).

    Returns:
        JSONResponse with ``{"exists": true}`` or ``{"exists": false}``.
    """
    normalized = name.strip()
    if not normalized:
        return JSONResponse(
            status_code=422,
            content={"detail": "name is required"},
        )
    db_path = _get_db_path(request)
    exists = await asyncio.to_thread(_check_target_name_exists, db_path, normalized)
    return JSONResponse(content={"exists": exists})


@router.get("/api/chain/templates")
async def api_chain_templates(request: Request) -> JSONResponse:
    """Return chain templates for the launcher dropdown."""
    from q_ai.chain.loader import load_all_chains

    try:
        chains = load_all_chains()
    except Exception:
        logger.exception("Failed to load chain templates")
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to load chain templates"},
        )
    templates = [{"id": c.id, "name": c.name, "category": c.category.value} for c in chains]
    return JSONResponse(content={"templates": templates})


@router.get("/api/chain/executions/recent")
async def api_chain_executions_recent(request: Request) -> JSONResponse:
    """Return recent successful chain executions for the blast-radius selector."""
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ce.id, ce.chain_name, ce.success, ce.created_at
            FROM chain_executions ce
            WHERE ce.success = 1
            ORDER BY ce.created_at DESC
            LIMIT 20
            """
        ).fetchall()
    return JSONResponse(content={"executions": [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# Workflow launch API
# ---------------------------------------------------------------------------

_VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}
_background_tasks: set[asyncio.Task[None]] = set()


def _validate_transport_and_model(
    body: dict[str, Any],
) -> JSONResponse | None:
    """Validate transport, command/url, and model fields from a workflow body.

    Args:
        body: The parsed request body dict.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    transport = body.get("transport", "").strip()
    if transport not in _VALID_TRANSPORTS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    f"Invalid transport. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
                ),
            },
        )

    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None

    if transport == "stdio" and not command:
        return JSONResponse(
            status_code=422,
            content={"detail": "command is required for stdio transport"},
        )
    if transport in ("sse", "streamable-http") and not url:
        return JSONResponse(
            status_code=422,
            content={"detail": "url is required for sse/streamable-http transport"},
        )

    model = body.get("model", "").strip()
    if not model or "/" not in model:
        return JSONResponse(
            status_code=422,
            content={"detail": "model must be non-empty and in provider/model format"},
        )

    return None


def _build_assess_config(body: dict[str, Any], target_id: str) -> dict[str, Any] | JSONResponse:
    """Build config for the assess workflow."""
    error = _validate_transport_and_model(body)
    if error is not None:
        return error

    transport = body.get("transport", "").strip()
    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None
    model = body.get("model", "").strip()

    raw_rounds = body.get("rounds", 1)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer"},
        )
    rounds = raw_rounds
    if not 1 <= rounds <= 10:
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer between 1 and 10"},
        )
    rxp_enabled = bool(body.get("rxp_enabled", False))

    # Extract inject technique filter (None = not specified, [] = none selected)
    raw_techniques = body.get("techniques")
    techniques: list[str] | None = None
    if isinstance(raw_techniques, list):
        techniques = [str(t) for t in raw_techniques]

    # Extract explicit payload name filter (overrides techniques when set)
    raw_payloads = body.get("payload_names")
    payloads: list[str] | None = None
    if isinstance(raw_payloads, list):
        payloads = [str(p) for p in raw_payloads]

    # Extract audit category filter (None = not specified, [] = none selected)
    raw_checks = body.get("checks")
    checks: list[str] | None = None
    if isinstance(raw_checks, list):
        checks = [str(c) for c in raw_checks]

    return {
        "target_id": target_id,
        "transport": transport,
        "command": command,
        "url": url,
        "rxp_enabled": rxp_enabled,
        "audit": {"checks": checks},
        "inject": {
            "model": model,
            "rounds": rounds,
            "techniques": techniques,
            "payloads": payloads,
        },
        "proxy": {"intercept": False},
    }


def _build_test_docs_config(body: dict[str, Any], target_id: str) -> dict[str, Any] | JSONResponse:
    """Build config for the test_docs workflow."""
    callback_url = body.get("callback_url", "").strip()
    if not callback_url:
        return JSONResponse(
            status_code=422,
            content={"detail": "callback_url is required"},
        )
    return {
        "target_id": target_id,
        "callback_url": callback_url,
        "output_dir": "",
        "format": body.get("format", "pdf"),
        "payload_style": body.get("payload_style", "obvious"),
        "payload_type": body.get("payload_type", "callback"),
        "base_name": "report",
        "rxp_enabled": bool(body.get("rxp_enabled", False)),
        "rxp": {
            "model_id": body.get("rxp_model_id", ""),
            "profile_id": body.get("rxp_profile_id") or None,
            "target_id": target_id,
        },
    }


def _build_test_assistant_config(
    body: dict[str, Any], target_id: str
) -> dict[str, Any] | JSONResponse:
    """Build config for the test_assistant workflow."""
    format_id = body.get("format_id", "").strip()
    if not format_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "format_id is required"},
        )
    return {
        "target_id": target_id,
        "format_id": format_id,
        "rule_ids": body.get("rule_ids") or None,
        "output_dir": "",
        "repo_name": body.get("repo_name", "").strip() or None,
    }


def _build_trace_path_config(
    body: dict[str, Any], target_id: str, db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Build config for the trace_path workflow."""
    from q_ai.chain.loader import discover_chains, load_chain

    template_id = body.get("chain_template_id", "").strip()
    if not template_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "chain_template_id is required"},
        )

    # Resolve template id to absolute path
    chain_file: str | None = None
    try:
        for path in discover_chains():
            chain = load_chain(path)
            if chain.id == template_id:
                chain_file = str(path.resolve())
                break
    except Exception:
        logger.debug("Failed to load chain templates", exc_info=True)

    if chain_file is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Chain template not found: {template_id}"},
        )

    # Validate transport and model (same rules as assess)
    error = _validate_transport_and_model(body)
    if error is not None:
        return error

    transport = body.get("transport", "").strip()
    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None
    model = body.get("model", "").strip()

    return {
        "target_id": target_id,
        "chain_file": chain_file,
        "transport": transport,
        "command": command,
        "url": url,
        "inject_model": model,
    }


def _build_blast_radius_config(
    body: dict[str, Any], db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Build config for the blast_radius workflow.

    Derives target_id from the chain execution's run row instead of
    creating a new target.
    """
    exec_id = body.get("chain_execution_id", "").strip()
    if not exec_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "chain_execution_id is required"},
        )
    with get_connection(db_path) as conn:
        exec_row = conn.execute(
            "SELECT ce.id, r.target_id FROM chain_executions ce "
            "JOIN runs r ON r.id = ce.run_id WHERE ce.id = ?",
            (exec_id,),
        ).fetchone()
    if exec_row is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Chain execution not found"},
        )
    target_id = exec_row["target_id"]
    return {
        "target_id": target_id,
        "chain_execution_id": exec_id,
    }


def _build_generate_report_config(
    body: dict[str, Any], db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Build config for the generate_report workflow.

    Validates that the target_id exists in the database.
    """
    target_id = body.get("target_id", "").strip()
    if not target_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "target_id is required"},
        )
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id FROM targets WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Target not found"},
        )
    return {
        "target_id": target_id,
        "from_date": body.get("from_date") or None,
        "to_date": body.get("to_date") or None,
        "include_evidence_pack": bool(body.get("include_evidence_pack", False)),
    }


async def _run_workflow(
    runner: WorkflowRunner,
    executor: Any,
    config: dict[str, Any],
) -> None:
    """Execute a workflow in the background, handling unexpected failures.

    Args:
        runner: The active WorkflowRunner instance.
        executor: Async executor function from the workflow registry.
        config: Workflow configuration dict.
    """
    try:
        await executor(runner, config)
    except Exception as exc:
        # Only fail if not already in terminal status
        with get_connection(runner._db_path) as conn:
            run = run_service.get_run(conn, runner.run_id)
        if run and run.status in (RunStatus.RUNNING, RunStatus.PENDING):
            await runner.fail(error=str(exc))


async def _validate_provider_model(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | None:
    """Validate provider/model pair before launch.

    Checks: provider is known, configured, model is non-empty, and
    local providers are reachable.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    provider_name = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()

    if not provider_name:
        # Backward compat: try to extract from model string
        if model and "/" in model:
            provider_name = model.split("/", 1)[0]
        if not provider_name:
            return JSONResponse(
                status_code=422,
                content={"detail": "provider is required"},
            )

    config = get_provider(provider_name)
    if config is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown provider: {provider_name}"},
        )

    # Check configured
    with get_connection(db_path) as conn:
        try:
            cred = get_credential(provider_name)
        except RuntimeError:
            cred = None
        base_url = get_setting(conn, f"{provider_name}.base_url") or ""

    configured = cred is not None or bool(base_url)
    if not configured and config.type != ProviderType.CUSTOM:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Provider '{provider_name}' is not configured"},
        )

    if not model:
        return JSONResponse(
            status_code=422,
            content={"detail": "No model selected"},
        )

    # For local providers, check reachability via fetch_models
    if config.type == ProviderType.LOCAL:
        result = await fetch_models(provider_name, base_url or None)
        if result.error:
            return JSONResponse(
                status_code=422,
                content={"detail": result.error},
            )

    return None


def _build_workflow_config(
    workflow_id: str, body: dict[str, Any], db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Dispatch to the appropriate config builder for the given workflow.

    Args:
        workflow_id: The workflow identifier string.
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        The workflow config dict on success, or a JSONResponse with error details.
    """
    builders: dict[str, Callable[[], dict[str, Any] | JSONResponse]] = {
        "assess": partial(_build_assess_config, body, ""),
        "test_docs": partial(_build_test_docs_config, body, ""),
        "test_assistant": partial(_build_test_assistant_config, body, ""),
        "trace_path": partial(_build_trace_path_config, body, "", db_path),
        "blast_radius": partial(_build_blast_radius_config, body, db_path),
        "generate_report": partial(_build_generate_report_config, body, db_path),
    }
    builder = builders.get(workflow_id)
    if builder is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"No builder for: {workflow_id}"},
        )
    return builder()


def _apply_target_id(config: dict[str, Any], target_id: str) -> None:
    """Set target_id on the config and any nested sub-configs.

    Args:
        config: The workflow configuration dict (mutated in place).
        target_id: The newly created target identifier.
    """
    config["target_id"] = target_id
    if "rxp" in config and isinstance(config["rxp"], dict):
        config["rxp"]["target_id"] = target_id


def _prepare_output_dir(
    workflow_id: str, run_id: str, config: dict[str, Any]
) -> JSONResponse | None:
    """Create the artifact output directory for workflows that need one.

    Applies to test_docs, test_assistant, and generate_report workflows.
    Sets ``config["output_dir"]`` on success.

    Args:
        workflow_id: The workflow identifier string.
        run_id: The run identifier for directory naming.
        config: The workflow configuration dict (mutated in place).

    Returns:
        A JSONResponse with 500 status on failure, or None on success.
    """
    if workflow_id == "generate_report":
        output_dir = Path.home() / ".qai" / "exports" / "generate_report" / run_id
    elif workflow_id in ("test_docs", "test_assistant"):
        output_dir = Path.home() / ".qai" / "artifacts" / workflow_id / run_id
    else:
        return None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Failed to create output directory for %s", workflow_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to prepare artifact output directory"},
        )
    config["output_dir"] = str(output_dir)
    return None


# ---------------------------------------------------------------------------
# Inject API routes
# ---------------------------------------------------------------------------


@router.get("/api/inject/payloads")
async def api_inject_payloads(request: Request) -> JSONResponse:
    """Return all inject payload template metadata.

    Returns:
        JSONResponse with a list of payload template metadata objects,
        each containing name, technique, owasp_ids, and description.
    """
    from q_ai.inject.payloads.loader import load_all_templates

    templates = load_all_templates()
    payload_data = [
        {
            "name": t.name,
            "technique": t.technique.value,
            "owasp_ids": t.owasp_ids,
            "description": t.description,
        }
        for t in templates
    ]
    return JSONResponse(content=payload_data)


@router.post("/api/workflows/launch")
async def launch_workflow(request: Request) -> JSONResponse:
    """Launch a workflow.

    Validates the request, dispatches to per-workflow config building,
    creates a target (where applicable), and starts the workflow as a
    background task.
    """
    body = await request.json()
    db_path = _get_db_path(request)

    # --- Resolve workflow ---
    workflow_id = body.get("workflow_id", "assess").strip()
    entry = get_workflow(workflow_id)
    if entry is None or entry.executor is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown workflow: {workflow_id}"},
        )

    # --- Provider/model validation (skipped for workflows that don't need one) ---
    if entry.requires_provider:
        validation_error = await _validate_provider_model(body, db_path)
        if validation_error is not None:
            return validation_error

    # --- Validate target_name early (but don't create row yet) ---
    _no_target_name_workflows = {"blast_radius", "generate_report"}
    target_name: str = ""
    if workflow_id not in _no_target_name_workflows:
        target_name = body.get("target_name", "").strip()
        if not target_name:
            return JSONResponse(
                status_code=422,
                content={"detail": "target_name is required"},
            )

    # --- Build workflow config (before target creation to avoid orphan rows) ---
    result = _build_workflow_config(workflow_id, body, db_path)
    if isinstance(result, JSONResponse):
        return result
    config: dict[str, Any] = result

    # --- Create target (only after builder succeeds, skip for existing-target workflows) ---
    if workflow_id not in _no_target_name_workflows:
        with get_connection(db_path) as conn:
            target_id = create_target(conn, type="server", name=target_name)
        _apply_target_id(config, target_id)

    # --- Create runner ---
    runner = WorkflowRunner(
        workflow_id=workflow_id,
        config=config,
        ws_manager=request.app.state.ws_manager,
        active_workflows=request.app.state.active_workflows,
        db_path=db_path,
        source="web",
    )

    # --- Set output_dir from run_id BEFORE start() persists config ---
    dir_error = _prepare_output_dir(workflow_id, runner.run_id, config)
    if dir_error is not None:
        return dir_error

    await runner.start()

    # --- Fire-and-forget background task ---
    task = asyncio.create_task(_run_workflow(runner, entry.executor, config))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        status_code=201,
        content={
            "run_id": runner.run_id,
            "redirect": f"/operations?run_id={runner.run_id}",
        },
    )


# ---------------------------------------------------------------------------
# Quick Actions launch API
# ---------------------------------------------------------------------------


def _str_field(body: dict[str, Any], key: str, default: str = "") -> str:
    """Extract a string field from a request body, rejecting non-string values.

    Args:
        body: The parsed request body dict.
        key: The field name to extract.
        default: Default value if key is missing or None.

    Returns:
        The stripped string value.

    Raises:
        TypeError: If the value is present but not a string (e.g. number,
            array, object).
    """
    val = body.get(key, default)
    if val is None:
        return default
    if not isinstance(val, str):
        raise TypeError(f"'{key}' must be a string")
    return val.strip()


_QUICK_ACTION_DISPLAY_NAMES = {
    "qa_scan": "Quick Audit Run",
    "qa_intercept": "Quick Proxy Run",
    "qa_campaign": "Quick Inject Run",
}

_QUICK_ACTIONS = {"scan", "intercept", "campaign"}

_QUICK_ACTION_PROVIDER_REQUIRED = {"campaign"}

_QUICK_ACTION_WORKFLOW_MAP = {
    "scan": "qa_scan",
    "intercept": "qa_intercept",
    "campaign": "qa_campaign",
}


def _validate_campaign_fields(body: dict[str, Any]) -> JSONResponse | None:
    """Validate campaign-specific fields (model and rounds).

    Args:
        body: The parsed request body dict.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    model = _str_field(body, "model")
    if not model or "/" not in model:
        return JSONResponse(
            status_code=422,
            content={"detail": "model must be non-empty and in provider/model format"},
        )
    raw_rounds = body.get("rounds", 1)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer"},
        )
    rounds = raw_rounds
    if not 1 <= rounds <= 10:
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer between 1 and 10"},
        )
    return None


async def _validate_quick_action(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | tuple[str, str]:
    """Validate quick action request fields.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        A JSONResponse on validation error, or a (action, target_name) tuple
        on success.
    """
    action = _str_field(body, "action")
    if action not in _QUICK_ACTIONS:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown action: {action}"},
        )

    if action in _QUICK_ACTION_PROVIDER_REQUIRED:
        validation_error = await _validate_provider_model(body, db_path)
        if validation_error is not None:
            return validation_error

    target_name = _str_field(body, "target_name")
    if not target_name:
        return JSONResponse(
            status_code=422,
            content={"detail": "target_name is required"},
        )

    transport_error = _validate_transport_and_command(body)
    if transport_error is not None:
        return transport_error

    if action == "campaign":
        campaign_error = _validate_campaign_fields(body)
        if campaign_error is not None:
            return campaign_error

    return action, target_name


def _validate_transport_and_command(body: dict[str, Any]) -> JSONResponse | None:
    """Validate transport and command/url fields from a request body.

    Args:
        body: The parsed request body dict.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    transport = _str_field(body, "transport")
    if transport not in _VALID_TRANSPORTS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    f"Invalid transport. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
                ),
            },
        )
    command = _str_field(body, "command") or None
    url = _str_field(body, "url") or None
    if transport == "stdio" and not command:
        return JSONResponse(
            status_code=422,
            content={"detail": "command is required for stdio transport"},
        )
    if transport in ("sse", "streamable-http") and not url:
        return JSONResponse(
            status_code=422,
            content={"detail": "url is required for sse/streamable-http transport"},
        )
    return None


def _build_quick_action_config(action: str, body: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Build config dict for a quick action.

    Args:
        action: The quick action name (scan, intercept, campaign).
        body: The parsed request body dict.
        target_id: The created target ID.

    Returns:
        Configuration dict for the quick action executor.
    """
    config: dict[str, Any] = {
        "target_id": target_id,
        "transport": _str_field(body, "transport"),
        "command": _str_field(body, "command") or None,
        "url": _str_field(body, "url") or None,
    }
    if action == "campaign":
        config["model"] = _str_field(body, "model")
        config["rounds"] = int(body.get("rounds", 1))
        raw_techniques = body.get("techniques")
        if isinstance(raw_techniques, list):
            config["techniques"] = [str(t) for t in raw_techniques]
    if action == "scan":
        raw_checks = body.get("checks")
        if isinstance(raw_checks, list):
            config["checks"] = [str(c) for c in raw_checks]
    return config


@router.post("/api/quick-actions/launch")
async def launch_quick_action(request: Request) -> JSONResponse:
    """Launch a single-module quick action.

    Validates the request body, creates a target and run, then executes
    the module operation in the background.

    Args:
        request: The incoming HTTP request with a JSON body containing
            action, target_name, transport, and action-specific fields.

    Returns:
        JSONResponse with 201 status containing run_id and redirect URL
        on success, or 400/422 on validation failure.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid JSON in request body"},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request body must be a JSON object"},
        )
    db_path = _get_db_path(request)

    try:
        result = await _validate_quick_action(body, db_path)
    except TypeError:
        logger.warning("Invalid request parameters in quick action", exc_info=True)
        return JSONResponse(status_code=422, content={"detail": "Invalid request parameters"})
    if isinstance(result, JSONResponse):
        return result
    action, target_name = result

    def _create_target_sync() -> str:
        with get_connection(db_path) as conn:
            return create_target(conn, type="server", name=target_name)

    target_id = await asyncio.to_thread(_create_target_sync)

    try:
        config = _build_quick_action_config(action, body, target_id)
    except TypeError:
        logger.warning("Invalid action configuration in quick action", exc_info=True)
        return JSONResponse(status_code=422, content={"detail": "Invalid action configuration"})

    runner = WorkflowRunner(
        workflow_id=_QUICK_ACTION_WORKFLOW_MAP[action],
        config=config,
        ws_manager=request.app.state.ws_manager,
        active_workflows=request.app.state.active_workflows,
        db_path=db_path,
        source="web",
    )
    await runner.start()

    from q_ai.orchestrator.workflows.quick_actions import (
        quick_campaign,
        quick_intercept,
        quick_scan,
    )

    executors = {
        "scan": quick_scan,
        "intercept": quick_intercept,
        "campaign": quick_campaign,
    }

    task = asyncio.create_task(_run_workflow(runner, executors[action], config))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        status_code=201,
        content={
            "run_id": runner.run_id,
            "redirect": f"/runs?run_id={runner.run_id}",
        },
    )


# ---------------------------------------------------------------------------
# Workflow resume API
# ---------------------------------------------------------------------------


@router.post("/api/workflows/{run_id}/resume")
async def resume_workflow(request: Request, run_id: str) -> JSONResponse:
    """Resume a workflow that is waiting for user action.

    Looks up the active WorkflowRunner for this run_id and calls resume().
    Returns 404 if no active runner exists, 409 if the run is not in
    WAITING_FOR_USER state (idempotent — duplicate clicks are safe).
    """
    active_workflows: dict[str, object] = request.app.state.active_workflows
    runner = active_workflows.get(run_id)
    if runner is None:
        return JSONResponse(status_code=404, content={"detail": "No active workflow for this run"})

    from q_ai.orchestrator.runner import WorkflowRunner

    if not isinstance(runner, WorkflowRunner):
        return JSONResponse(status_code=500, content={"detail": "Invalid runner type"})

    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
    if run is None or run.status != RunStatus.WAITING_FOR_USER:
        return JSONResponse(
            status_code=409,
            content={"detail": "Workflow is not waiting for user action"},
        )

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        body: dict = {}
    else:
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid JSON in request body"},
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"detail": "Request body must be a JSON object"},
            )
    await runner.resume(body)
    return JSONResponse(content={"status": "resumed"})


# ---------------------------------------------------------------------------
# Conclude Campaign API
# ---------------------------------------------------------------------------


def _sync_conclude(
    db_path: Path | None,
    run_id: str,
) -> str:
    """Run the conclude-campaign DB work synchronously.

    Returns:
        ``"not_found"``, ``"already_terminal"``, or ``"concluded"``.
    """
    terminal_ints = tuple(int(s) for s in _TERMINAL_STATUSES)
    now = _dt.datetime.now(_dt.UTC).isoformat()

    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
        if run is None:
            return "not_found"
        if run.status in _TERMINAL_STATUSES:
            return "already_terminal"

        # Atomic conditional UPDATE — only transitions non-terminal rows
        non_terminal_ph = ", ".join("?" for _ in terminal_ints)
        conn.execute(
            f"UPDATE runs SET status = ?, finished_at = ? "  # noqa: S608
            f"WHERE id = ? AND status NOT IN ({non_terminal_ph})",
            (int(RunStatus.COMPLETED), now, run_id, *terminal_ints),
        )
        # Transition children still in WAITING_FOR_USER
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE parent_run_id = ? AND status = ?",
            (int(RunStatus.COMPLETED), now, run_id, int(RunStatus.WAITING_FOR_USER)),
        )

    return "concluded"


@router.post("/api/workflows/{run_id}/conclude")
async def conclude_campaign(request: Request, run_id: str) -> JSONResponse:
    """Conclude a research campaign, transitioning the run to COMPLETED.

    Marks the parent run and any children still in WAITING_FOR_USER as
    COMPLETED with finished_at. Idempotent: already-terminal runs return
    success. Emits a run_status WebSocket event and unblocks the runner's
    wait event so the adapter coroutine exits cleanly.
    """
    db_path = _get_db_path(request)
    result = await asyncio.to_thread(_sync_conclude, db_path, run_id)

    if result == "not_found":
        return JSONResponse(status_code=404, content={"detail": "Run not found"})
    if result == "already_terminal":
        return JSONResponse(content={"status": "concluded"})

    # Emit WebSocket event so UI updates live
    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast(
        {
            "type": "run_status",
            "run_id": run_id,
            "status": int(RunStatus.COMPLETED),
            "module": "workflow",
        }
    )

    # Unblock the runner's wait event so the adapter coroutine exits cleanly
    active_workflows: dict[str, object] = request.app.state.active_workflows
    runner = active_workflows.get(run_id)
    if runner is not None and isinstance(runner, WorkflowRunner):
        runner.unblock()
        active_workflows.pop(run_id, None)

    return JSONResponse(content={"status": "concluded"})


# ---------------------------------------------------------------------------
# IPI Hit Bridge (internal)
# ---------------------------------------------------------------------------


@router.post("/api/internal/ipi-hit")
async def api_internal_ipi_hit(request: Request) -> JSONResponse:
    """Receive a hit notification from the IPI callback server.

    Validates the bridge token (cached at app startup), reads the
    canonical hit from the DB, and broadcasts an ``ipi_hit`` WebSocket
    event. Non-creating: never writes or mutates hit records.

    Args:
        request: The incoming FastAPI request. Must include an
            ``X-QAI-Bridge-Token`` header matching the cached token
            and a JSON body with ``{"hit_id": "<id>"}``.

    Returns:
        JSONResponse with ``{"status": "ok"}`` on success, 401 if the
        bridge token is missing or invalid, 400 if the body is malformed,
        or 404 if the hit ID does not exist in the database.
    """
    token = request.headers.get("X-QAI-Bridge-Token")
    expected: str | None = request.app.state.bridge_token
    if not token or not expected or token != expected:
        return JSONResponse(status_code=401, content={"detail": "Invalid bridge token"})

    try:
        body = await request.json()
    except (ValueError, _json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "Expected JSON object"})

    hit_id = body.get("hit_id")
    if not hit_id:
        return JSONResponse(status_code=400, content={"detail": "Missing hit_id"})

    db_path = _get_db_path(request)

    def _read_hit() -> dict[str, Any] | None:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id, uuid, source_ip, user_agent, confidence,"
                " token_valid, timestamp, body"
                " FROM ipi_hits WHERE id = ?",
                (hit_id,),
            ).fetchone()
            return dict(row) if row else None

    hit_data = await asyncio.to_thread(_read_hit)
    if not hit_data:
        return JSONResponse(status_code=404, content={"detail": "Hit not found"})

    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast({"type": "ipi_hit", **hit_data})
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Audit API routes
# ---------------------------------------------------------------------------


@router.post("/api/audit/scan")
async def api_audit_scan(request: Request) -> HTMLResponse:
    """Start an audit scan in the background."""
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request, "partials/audit_tab.html", {"scan_status": "submitted"}
    )


@router.get("/api/audit/scan/{run_id}/status")
async def api_audit_scan_status(request: Request, run_id: str) -> HTMLResponse:
    """Return scan progress partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
    status = run.status.name if run is not None else "UNKNOWN"
    return templates.TemplateResponse(request, "partials/audit_tab.html", {"scan_status": status})


@router.post("/api/audit/enumerate")
async def api_audit_enumerate(request: Request) -> JSONResponse:
    """Enumerate an MCP server's capabilities without scanning.

    Connects to the server, lists tools/resources/prompts, and returns
    the result as JSON. Does not create a run or persist to the database.

    Args:
        request: The incoming HTTP request with JSON body containing
            transport, command/url fields.

    Returns:
        JSONResponse with server_info, tools, resources, prompts on success,
        or 422 on validation error, or 500 on connection failure.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422, content={"detail": "Request body must be a JSON object"}
        )

    transport_error = _validate_transport_and_command(body)
    if transport_error is not None:
        return transport_error

    from q_ai.audit.adapter import _build_connection
    from q_ai.mcp.discovery import enumerate_server

    try:
        conn = _build_connection(body)
        async with conn:
            context = await enumerate_server(conn)
        return JSONResponse(
            content={
                "server_info": context.server_info,
                "tools": context.tools,
                "resources": context.resources,
                "prompts": context.prompts,
            }
        )
    except Exception:
        logger.exception("Enumerate failed")
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to enumerate server"},
        )


@router.get("/api/audit/findings/{run_id}")
async def api_audit_findings(request: Request, run_id: str) -> HTMLResponse:
    """Return findings partial for a specific scan run."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        findings = finding_service.list_findings(conn, run_id=run_id)
    return templates.TemplateResponse(
        request, "partials/audit_findings.html", {"findings": findings}
    )


# ---------------------------------------------------------------------------
# Proxy API routes
# ---------------------------------------------------------------------------


@router.get("/api/proxy/sessions")
async def api_proxy_sessions(request: Request) -> HTMLResponse:
    """Return proxy sessions list partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ps.id, ps.run_id, ps.transport, ps.server_name,
                   ps.message_count, ps.duration_seconds, ps.created_at
            FROM proxy_sessions ps
            ORDER BY ps.created_at DESC
            LIMIT 50
            """
        ).fetchall()
    sessions = [dict(row) for row in rows]
    return templates.TemplateResponse(request, "partials/proxy_tab.html", {"sessions": sessions})


@router.get("/api/proxy/sessions/{run_id}")
async def api_proxy_session_detail(request: Request, run_id: str) -> HTMLResponse:
    """Return proxy session detail partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM proxy_sessions WHERE run_id = ?", (run_id,)).fetchone()
    session_data: dict[str, Any] = dict(row) if row else {}

    # Load message summary from session JSON if available
    messages_summary: list[dict[str, Any]] = []
    if session_data.get("session_file"):
        artifacts_dir = _ARTIFACTS_BASE.resolve()
        session_file = session_data["session_file"]
        # Reject path traversal attempts
        session_path = (artifacts_dir / session_file).resolve()
        if not session_path.is_relative_to(artifacts_dir):
            session_path = None  # type: ignore[assignment]
        if session_path and session_path.is_file():
            try:
                raw = _json.loads(session_path.read_text(encoding="utf-8"))
            except (_json.JSONDecodeError, OSError):
                raw = {}
            for msg in raw.get("messages", [])[:100]:
                direction = msg.get("direction", "")
                arrow = "\u2192" if direction == "client_to_server" else "\u2190"
                messages_summary.append(
                    {
                        "sequence": msg.get("sequence"),
                        "direction": arrow,
                        "method": msg.get("method") or "(response)",
                    }
                )

    return templates.TemplateResponse(
        request,
        "partials/proxy_tab.html",
        {"session_detail": session_data, "messages_summary": messages_summary},
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
