"""Runs, findings, and export route handlers."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json as _json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from q_ai.audit.reporting.csv_report import generate_csv_report
from q_ai.audit.reporting.ndjson_report import generate_ndjson_report
from q_ai.core.db import (
    delete_run_cascade,
    export_run_bundle,
    get_connection,
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
from q_ai.services import finding_service, run_service

router = APIRouter()

_STATUS_NAMES = [s.name for s in RunStatus]

_SEV_MAP = {4: "Critical", 3: "High", 2: "Medium", 1: "Low", 0: "Info"}

_ARTIFACTS_BASE = Path.home() / ".qai" / "artifacts"

_MAX_BULK_RUNS = 50


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


def _payload_template_map(inject_results_data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the payload-template lookup used by inject result drill-down.

    Loads templates from disk on demand — purely a presentation concern,
    so it stays in the route layer rather than the run service.
    """
    if not inject_results_data:
        return {}
    from q_ai.inject.payloads.loader import load_all_templates as _load_inject_templates

    return {
        tmpl.name: {
            "tool_description": tmpl.tool_description,
            "test_query": tmpl.test_query or f"Use the {tmpl.tool_name} tool.",
        }
        for tmpl in _load_inject_templates()
    }


def _resolve_workflow_display_name(name: str | None) -> str:
    """Resolve a workflow run name to its user-facing display name."""
    wf = get_workflow(name) if name else None
    if wf is not None:
        return wf.name
    return _QUICK_ACTION_DISPLAY_NAMES.get(name or "", name or "Workflow")


def _resolve_import_display_name(source: str | None) -> str:
    """Resolve an import run's source field to its user-facing display name."""
    return f"Import ({(source or 'Unknown').title()})"


def _safe_run_guidance(raw: Any, child_id: str) -> RunGuidance | None:
    """Parse a child run's guidance JSON into a ``RunGuidance``.

    Returns ``None`` on any shape mismatch or validation failure and logs
    at debug level — malformed guidance is a UI concern, never a crash.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        logger.debug("Child run %s guidance is not valid JSON", child_id)
        return None
    if not isinstance(parsed, dict):
        logger.debug("Child run %s guidance is not a JSON object", child_id)
        return None
    try:
        return RunGuidance.from_dict(parsed)
    except (TypeError, ValueError, KeyError):
        logger.debug("Child run %s guidance failed RunGuidance validation", child_id)
        return None


def _build_runs_context(db_path: Path | None, run_id: str) -> dict[str, Any]:
    """Build the template context for the single-run results view.

    Delegates DB queries to :func:`run_service.query_run_detail` and adds
    presentation-only data: workflow display name, payload template map,
    mitigation section labels, child-run guidance, and report HTML.
    """
    with get_connection(db_path) as conn:
        detail = run_service.query_run_detail(conn, run_id)
        if detail is None:
            return {"previously_seen": set()}

    workflow = get_workflow(detail.workflow_run.name) if detail.workflow_run.name else None
    if detail.workflow_run.module == "import":
        wf_name = _resolve_import_display_name(detail.workflow_run.source)
    else:
        wf_name = _resolve_workflow_display_name(detail.workflow_run.name)
    wf_modules = list(workflow.modules) if workflow else []

    module_data = dict(detail.module_data)
    module_data["payload_template_map"] = _payload_template_map(
        module_data.get("inject_results_data", [])
    )
    module_data["mitigation_section_label"] = _mitigation_section_label
    module_data["child_guidance"] = {
        mod: _safe_run_guidance(child.guidance, child.id)
        for mod, child in detail.child_by_module.items()
    }

    is_report_run = detail.workflow_run.name == "generate_report"
    report_html = ""
    has_evidence_zip = False
    report_run_id = detail.report_run_id
    if is_report_run:
        report_html, has_evidence_zip = _load_report_html(run_id)
        # For report runs, the run itself is the report_run_id
        report_run_id = run_id

    result: dict[str, Any] = {
        "workflow_run": detail.workflow_run,
        "child_runs": detail.child_runs,
        "findings": detail.findings,
        "results_mode": True,
        "is_terminal": detail.workflow_run.status in _TERMINAL_STATUSES,
        "workflow_display_name": wf_name,
        "duration_display": run_service.compute_duration(detail.workflow_run),
        "finding_counts": _count_findings_by_severity(detail.findings),
        "workflow_modules": wf_modules,
        "child_by_module": detail.child_by_module,
        "target": detail.target,
        "report_run_id": report_run_id,
        "report_html": report_html,
        "is_report_run": is_report_run,
        "has_evidence_zip": has_evidence_zip,
        "previously_seen": detail.previously_seen,
    }
    result.update(module_data)
    result["has_audit_findings"] = bool(module_data.get("audit_findings"))
    return result


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
    """Build the template context for the run-history view.

    Delegates DB queries and history-row assembly to
    :func:`run_service.query_history_runs` and adds the presentation-only
    keys (workflow filter list, status names, current filter selections).
    """
    parsed_status = _parse_status(status_filter)
    with get_connection(db_path) as conn:
        result = run_service.query_history_runs(
            conn,
            workflow_filter=workflow_filter or None,
            target_filter=target_filter or None,
            status=parsed_status,
            resolve_workflow_display_name=_resolve_workflow_display_name,
            resolve_import_display_name=_resolve_import_display_name,
        )

    return {
        "history_runs": result.history_runs,
        "workflows": list_workflows(),
        "targets": result.targets,
        "statuses": _STATUS_NAMES,
        "current_workflow": workflow_filter or "",
        "current_target": target_filter or "",
        "current_status": status_filter or "",
        "group_by_target": group_by_target,
        "prior_run_counts": result.prior_run_counts,
    }


@router.get("/runs")
async def runs(
    request: Request,
    run_id: str | None = Query(None),
    workflow: str | None = Query(None),
    target_id: str | None = Query(None),
    status: str | None = Query(None),
    group_by_target: str | None = Query(None),
    intel: str | None = Query(None),
) -> Response:
    """Render the runs view — history list or single-run results.

    For target-bound probe runs, requests without the ``intel`` query
    marker are 302-redirected to the target's Intel detail page
    (Phase 3 two-release migration). Intra-Intel links carry
    ``?run_id=<id>&intel=1`` and render the existing runs.html view.
    The redirect is 302 (not permanent) so removal in Phase 6 does not
    leave bookmarked users stranded by a cached response.
    """
    from q_ai.server.routes.assist import _get_suggested_prompts

    templates = _get_templates(request)
    db_path = _get_db_path(request)

    # When the request carries the ``intel`` bypass marker, in-page
    # navigation that re-hits this handler (e.g. status_bar's Refresh
    # link) must preserve the marker — otherwise the Phase 3 probe
    # redirect fires on the next click and bounces the user out of the
    # runs view. The suffix is consumed by templates that emit
    # ``/runs?run_id=…`` links while the user is in intra-Intel flow.
    runs_link_suffix = "&intel=1" if intel is not None else ""

    ctx: dict[str, Any] = {
        "active": "runs",
        "run_id": run_id,
        "runs_link_suffix": runs_link_suffix,
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
        # Two-release redirect: target-bound probe runs whose request
        # lacks the bypass marker go to the Intel detail page. Reads
        # `workflow_run` from the already-built context — no second DB
        # fetch. Unknown run_id falls out for free: _build_runs_context
        # returns `workflow_run=None` in that case, skipping the branch.
        wf_run = run_ctx.get("workflow_run")
        if (
            intel is None
            and wf_run is not None
            and wf_run.module == "ipi-probe"
            and wf_run.target_id
        ):
            return RedirectResponse(
                url=(f"/intel/targets/{quote(wf_run.target_id)}#probe-run-{quote(wf_run.id)}"),
                status_code=302,
                headers={"Cache-Control": "no-store"},
            )
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
        modules = list(ctx.get("child_by_module", {}).keys())

        def _load_assist_panel() -> dict[str, Any]:
            with get_connection(db_path) as conn:
                provider = get_setting(conn, "assist.provider") or ""
                model = get_setting(conn, "assist.model") or ""
                prompts = _get_suggested_prompts(conn, page="run_results", modules=modules)
            return {
                "assist_configured": bool(provider and model),
                "assist_prompts": prompts,
            }

        panel_ctx = await asyncio.to_thread(_load_assist_panel)
        ctx.update(panel_ctx)

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
        "left_duration": run_service.compute_duration(left_run),
        "right_duration": run_service.compute_duration(right_run),
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


def _list_findings_filtered(
    db_path: Path | None,
    module: str | None,
    category: str | None,
    min_severity: Severity | None,
) -> list:
    """Load findings with optional filters (blocking SQLite)."""
    with get_connection(db_path) as conn:
        return finding_service.list_findings(
            conn,
            module=module,
            category=category,
            min_severity=min_severity,
        )


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
    findings = await asyncio.to_thread(
        _list_findings_filtered,
        db_path,
        module or None,
        category or None,
        parsed_severity,
    )
    return templates.TemplateResponse(
        request, "partials/findings_table.html", {"findings": findings}
    )


def _list_all_targets(db_path: Path | None) -> list:
    """Load all targets (blocking SQLite)."""
    with get_connection(db_path) as conn:
        return list_targets(conn)


@router.get("/api/targets")
async def api_targets(request: Request) -> HTMLResponse:
    """Return the targets table partial for HTMX swap."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    targets = await asyncio.to_thread(_list_all_targets, db_path)
    return templates.TemplateResponse(request, "partials/targets_table.html", {"targets": targets})


def _get_child_runs_sync(db_path: Path | None, run_id: str) -> list:
    """Load child runs for a workflow (blocking SQLite)."""
    with get_connection(db_path) as conn:
        return run_service.get_child_runs(conn, run_id)


@router.get("/api/operations/status-bar")
async def operations_status_bar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render child run badges for the given workflow run."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    child_runs = await asyncio.to_thread(_get_child_runs_sync, db_path, run_id)
    return templates.TemplateResponse(
        request,
        "partials/child_run_badges.html",
        {"child_runs": child_runs},
    )


def _get_run_sync(db_path: Path | None, run_id: str) -> Any:
    """Load a single run row (blocking SQLite)."""
    with get_connection(db_path) as conn:
        return run_service.get_run(conn, run_id)


@router.get("/api/operations/workflow-status-bar")
async def operations_workflow_status_bar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render the full workflow status bar partial (badge, elapsed, report link)."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    workflow_run = await asyncio.to_thread(_get_run_sync, db_path, run_id)
    wf = get_workflow(workflow_run.name) if workflow_run and workflow_run.name else None
    display_name = wf.name if wf else (workflow_run.name if workflow_run else "Workflow")
    return templates.TemplateResponse(
        request,
        "partials/status_bar.html",
        {"workflow_run": workflow_run, "workflow_display_name": display_name},
    )


def _get_findings_for_run_sync(db_path: Path | None, run_id: str) -> list:
    """Load findings for a run (blocking SQLite)."""
    with get_connection(db_path) as conn:
        return finding_service.get_findings_for_run(conn, run_id)


@router.get("/api/operations/findings-sidebar")
async def operations_findings_sidebar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render the findings sidebar for the given workflow run."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    findings = await asyncio.to_thread(_get_findings_for_run_sync, db_path, run_id)
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
    if not isinstance(raw, dict):
        raw = {}

    raw_msgs = raw.get("messages", [])
    if not isinstance(raw_msgs, list):
        return [], 0
    all_msgs: list[dict[str, Any]] = [m for m in raw_msgs if isinstance(m, dict)]
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
