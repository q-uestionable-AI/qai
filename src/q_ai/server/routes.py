"""Route handlers for the q-ai web UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from q_ai.core.db import get_connection, get_run, list_findings, list_runs, list_targets
from q_ai.core.models import RunStatus, Severity

router = APIRouter()


def _get_templates(request: Request) -> Jinja2Templates:
    """Get the Jinja2Templates instance from app state."""
    result: Jinja2Templates = request.app.state.templates
    return result


def _get_db_path(request: Request) -> Path | None:
    """Get the database path from app state."""
    result: Path | None = request.app.state.db_path
    return result


# ---------------------------------------------------------------------------
# Full-page routes
# ---------------------------------------------------------------------------


@router.get("/")
async def launcher(request: Request) -> HTMLResponse:
    """Render the workflow launcher page."""
    templates = _get_templates(request)
    workflows: list[dict[str, Any]] = [
        {
            "name": "Assess an MCP Server",
            "description": (
                "Scan, intercept, and test tool trust boundaries in Model Context Protocol servers."
            ),
            "modules": ["audit", "proxy", "inject"],
        },
        {
            "name": "Test Document Ingestion",
            "description": (
                "Generate payloads for document pipelines and track execution callbacks."
            ),
            "modules": ["ipi", "rxp"],
        },
        {
            "name": "Test a Coding Assistant",
            "description": (
                "Poison context files and validate whether AI assistants propagate tainted output."
            ),
            "modules": ["cxp"],
        },
        {
            "name": "Trace an Attack Path",
            "description": (
                "Compose individual vulnerabilities into multi-step exploitation chains."
            ),
            "modules": ["chain"],
        },
        {
            "name": "Measure Blast Radius",
            "description": ("Analyze reach from a compromise point and generate detection rules."),
            "modules": ["chain"],
        },
        {
            "name": "Manage Research",
            "description": (
                "Campaigns, evidence collection, reports, and CVE tracking across all modules."
            ),
            "modules": ["audit", "proxy", "inject", "ipi", "cxp", "rxp", "chain"],
        },
    ]
    return templates.TemplateResponse(
        request, "launcher.html", {"active": "launcher", "workflows": workflows}
    )


@router.get("/operations")
async def operations(request: Request) -> HTMLResponse:
    """Render the operations skeleton view."""
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request,
        "operations.html",
        {
            "active": "operations",
            "findings": [],
            "scan_status": None,
            "campaign_status": None,
        },
    )


@router.get("/research")
async def research(request: Request) -> HTMLResponse:
    """Render the research workspace page."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        runs = list_runs(conn)
        findings = list_findings(conn)
        targets = list_targets(conn)
    return templates.TemplateResponse(
        request,
        "research.html",
        {
            "active": "research",
            "runs": runs,
            "findings": findings,
            "targets": targets,
            "modules": ["audit", "proxy", "inject", "ipi", "cxp", "rxp", "chain"],
            "severities": [s.name for s in Severity],
            "statuses": [s.name for s in RunStatus],
        },
    )


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
    with get_connection(db_path) as conn:
        runs = list_runs(
            conn, module=module or None, status=parsed_status, target_id=target_id or None
        )
    return templates.TemplateResponse(request, "partials/runs_table.html", {"runs": runs})


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
        findings = list_findings(
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


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for live updates.

    Accepts the connection and sends a status message. Infrastructure only --
    no event broadcasting yet. This will be used in Phase 4/5.
    """
    await websocket.accept()
    await websocket.send_json({"status": "connected"})
    try:
        while True:
            await websocket.receive_text()
    except Exception:  # noqa: S110  # WebSocket disconnect is expected
        pass


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
        run = get_run(conn, run_id)
    status = run.status.name if run is not None else "UNKNOWN"
    return templates.TemplateResponse(request, "partials/audit_tab.html", {"scan_status": status})


@router.get("/api/audit/findings/{run_id}")
async def api_audit_findings(request: Request, run_id: str) -> HTMLResponse:
    """Return findings partial for a specific scan run."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        findings = list_findings(conn, run_id=run_id)
    return templates.TemplateResponse(
        request, "partials/audit_findings.html", {"findings": findings}
    )


# ---------------------------------------------------------------------------
# Inject API routes
# ---------------------------------------------------------------------------


@router.post("/api/inject/campaign")
async def api_inject_campaign(request: Request) -> HTMLResponse:
    """Start an inject campaign (placeholder -- returns status)."""
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request, "partials/inject_tab.html", {"campaign_status": "submitted"}
    )


@router.get("/api/inject/campaign/{run_id}/status")
async def api_inject_campaign_status(request: Request, run_id: str) -> HTMLResponse:
    """Return inject campaign progress partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        run = get_run(conn, run_id)
    status = run.status.name if run is not None else "UNKNOWN"
    return templates.TemplateResponse(
        request, "partials/inject_tab.html", {"campaign_status": status}
    )


@router.get("/api/inject/results/{run_id}")
async def api_inject_results(request: Request, run_id: str) -> HTMLResponse:
    """Return inject results partial for a specific campaign run."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        findings = list_findings(conn, run_id=run_id)
    return templates.TemplateResponse(
        request, "partials/findings_table.html", {"findings": findings}
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
    import json

    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM proxy_sessions WHERE run_id = ?", (run_id,)).fetchone()
    session_data: dict[str, Any] = dict(row) if row else {}

    # Load message summary from session JSON if available
    messages_summary: list[dict[str, Any]] = []
    if session_data.get("session_file"):
        artifacts_dir = Path.home() / ".qai" / "artifacts"
        session_path = artifacts_dir / session_data["session_file"]
        if session_path.exists():
            raw = json.loads(session_path.read_text(encoding="utf-8"))
            for msg in raw.get("messages", [])[:100]:
                direction = msg.get("direction", "")
                arrow = "\u2192" if "client" in direction else "\u2190"
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
