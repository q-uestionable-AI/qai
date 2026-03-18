"""Route handlers for the q-ai web UI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketDisconnect

from q_ai.core.config import delete_credential, get_credential, set_credential
from q_ai.core.db import (
    create_target,
    get_connection,
    get_run,
    get_setting,
    list_findings,
    list_runs,
    list_targets,
    set_setting,
)
from q_ai.core.models import RunStatus, Severity
from q_ai.orchestrator.registry import get_workflow, list_workflows
from q_ai.orchestrator.runner import WorkflowRunner
from q_ai.rxp._deps import is_available as rxp_is_available

logger = logging.getLogger(__name__)

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
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "modules": wf.modules,
            "implemented": wf.executor is not None,
            "requires_provider": wf.requires_provider,
        }
        for wf in list_workflows()
    ]
    all_providers = _get_providers_status(request)
    providers = [p for p in all_providers if p["configured"]]
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        defaults = {
            "ipi_callback_url": get_setting(conn, "ipi.default_callback_url") or "",
        }
    return templates.TemplateResponse(
        request,
        "launcher.html",
        {
            "active": "launcher",
            "workflows": workflows,
            "providers": providers,
            "rxp_available": rxp_is_available(),
            "defaults": defaults,
        },
    )


@router.get("/operations")
async def operations(
    request: Request,
    run_id: str | None = Query(None),
) -> HTMLResponse:
    """Render the operations view with optional workflow state."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)

    workflow_run = None
    child_runs: list[Any] = []
    findings: list[Any] = []

    if run_id:
        with get_connection(db_path) as conn:
            workflow_run = get_run(conn, run_id)
            if workflow_run:
                child_runs = list_runs(conn, parent_run_id=run_id)

                # OPTIMIZATION: Fix N+1 query. Instead of looping through child_runs
                # and querying list_findings for each, we gather all run IDs and fetch
                # findings in a single query reducing O(N) queries to O(1).
                all_run_ids = [run_id] + [child.id for child in child_runs]
                findings = list_findings(conn, run_ids=all_run_ids)

    return templates.TemplateResponse(
        request,
        "operations.html",
        {
            "active": "operations",
            "run_id": run_id,
            "workflow_run": workflow_run,
            "child_runs": child_runs,
            "findings": findings,
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


@router.get("/api/operations/status-bar")
async def operations_status_bar(
    request: Request,
    run_id: str = Query(...),
) -> HTMLResponse:
    """Render child run badges for the given workflow run."""
    db_path = _get_db_path(request)
    templates = _get_templates(request)
    with get_connection(db_path) as conn:
        child_runs = list_runs(conn, parent_run_id=run_id)
    return templates.TemplateResponse(
        request,
        "partials/child_run_badges.html",
        {"child_runs": child_runs},
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
        all_ids = [run_id] + [c.id for c in list_runs(conn, parent_run_id=run_id)]
        findings = list_findings(conn, run_ids=all_ids)
    return templates.TemplateResponse(
        request,
        "partials/findings_sidebar.html",
        {"findings": findings},
    )


# ---------------------------------------------------------------------------
# Report export route
# ---------------------------------------------------------------------------

_EXPORTS_BASE = Path.home() / ".qai" / "exports"


@router.get("/api/exports/{run_id}/report")
async def export_report(request: Request, run_id: str) -> FileResponse:
    """Serve a generated report.md file for the given run.

    Validates that the run_id exists in the database and that the report
    file is within the expected exports directory (path traversal prevention).
    """
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        run = get_run(conn, run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"detail": "Run not found"})  # type: ignore[return-value]

    report_path = (_EXPORTS_BASE / "generate_report" / run_id / "report.md").resolve()
    if not str(report_path).startswith(str(_EXPORTS_BASE.resolve())):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})  # type: ignore[return-value]

    if not report_path.is_file():
        return JSONResponse(status_code=404, content={"detail": "Report not found"})  # type: ignore[return-value]

    return FileResponse(
        path=report_path,
        media_type="text/markdown; charset=utf-8",
        filename=f"report-{run_id[:12]}.md",
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
    known_providers = [
        "anthropic",
        "openai",
        "groq",
        "openrouter",
        "ollama",
        "lmstudio",
        "custom",
    ]
    db_path = _get_db_path(request)
    result: list[dict[str, Any]] = []
    with get_connection(db_path) as conn:
        for p in known_providers:
            keyring_unavailable = False
            try:
                cred = get_credential(p)
            except RuntimeError:
                cred = None
                keyring_unavailable = True
            base_url = get_setting(conn, f"{p}.base_url") or ""
            configured = cred is not None or bool(base_url)
            result.append(
                {
                    "name": p,
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
            "default_model": get_setting(conn, "default_model") or "",
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
        }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"active": "settings", "providers": providers_status, "defaults": defaults},
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
            "default_model": get_setting(conn, "default_model") or "",
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
        }
    return JSONResponse(content=defaults)


@router.post("/api/settings/defaults")
async def api_save_defaults(request: Request) -> JSONResponse:
    """Save default settings to DB."""
    body = await request.json()
    db_path = _get_db_path(request)
    allowed_keys = (
        "default_model",
        "audit.default_transport",
        "ipi.default_callback_url",
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

    try:
        rounds = int(body.get("rounds", 1))
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer"},
        )
    if not 1 <= rounds <= 10:
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer between 1 and 10"},
        )
    rxp_enabled = bool(body.get("rxp_enabled", False))
    return {
        "target_id": target_id,
        "transport": transport,
        "command": command,
        "url": url,
        "rxp_enabled": rxp_enabled,
        "audit": {"checks": None},
        "inject": {"model": model, "rounds": rounds},
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
            run = get_run(conn, runner.run_id)
        if run and run.status in (RunStatus.RUNNING, RunStatus.PENDING):
            await runner.fail(error=str(exc))


def _check_provider_credential(body: dict[str, Any], db_path: Path | None) -> JSONResponse | None:
    """Validate that the provider referenced by the model field has credentials.

    For local providers (ollama, lmstudio, custom), checks that a base URL is
    configured or has a default. For cloud providers, checks that a credential
    exists in the keyring.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    model = body.get("model", "").strip()
    if not model or "/" not in model:
        return JSONResponse(
            status_code=422,
            content={"detail": "model must be non-empty and in provider/model format"},
        )
    provider = model.split("/", 1)[0]
    local_providers = {"ollama", "lmstudio", "custom"}
    if provider in local_providers:
        default_urls = {
            "ollama": "http://localhost:11434",
            "lmstudio": "http://localhost:1234",
        }
        with get_connection(db_path) as conn:
            base_url_setting = get_setting(conn, f"{provider}.base_url")
        if not base_url_setting and provider not in default_urls:
            return JSONResponse(
                status_code=422,
                content={"detail": f"No base URL configured for provider '{provider}'"},
            )
    else:
        try:
            cred = get_credential(provider)
        except RuntimeError:
            cred = None
        if cred is None:
            return JSONResponse(
                status_code=422,
                content={"detail": f"No credential configured for provider '{provider}'"},
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

    # --- Provider credential check (skipped for workflows that don't need one) ---
    if entry.requires_provider:
        cred_error = _check_provider_credential(body, db_path)
        if cred_error is not None:
            return cred_error

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
        run = get_run(conn, run_id)
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
