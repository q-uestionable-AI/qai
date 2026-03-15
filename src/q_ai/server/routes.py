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
                SELECT step_id, module, technique, success, status, error
                FROM chain_step_outputs
                WHERE execution_id = ?
                ORDER BY created_at
                """,
                (exec_row["id"],),
            ).fetchall()
    execution_detail: dict[str, Any] = dict(exec_row) if exec_row else {}
    step_outputs = [dict(row) for row in step_rows]
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
        session_file = session_data["session_file"]
        # Reject path traversal attempts
        session_path = (artifacts_dir / session_file).resolve()
        if not str(session_path).startswith(str(artifacts_dir.resolve())):
            session_path = None  # type: ignore[assignment]
        if session_path and session_path.exists():
            try:
                raw = json.loads(session_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
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
