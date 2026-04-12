"""Audit module routes — scan, enumerate, findings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import (
    _get_db_path,
    _get_templates,
    logger,
)
from q_ai.services import finding_service, run_service

router = APIRouter()


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
    from q_ai.server.routes.workflows import _validate_transport_and_command

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
