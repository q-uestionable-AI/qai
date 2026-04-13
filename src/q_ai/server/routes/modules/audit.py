"""Audit module routes — scan, enumerate, findings."""

from __future__ import annotations

import asyncio
from pathlib import Path

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


def _get_run_status(db_path: Path | None, run_id: str) -> str:
    """Return the named status of a run, or ``UNKNOWN``."""
    with get_connection(db_path) as conn:
        run = run_service.get_run(conn, run_id)
    return run.status.name if run is not None else "UNKNOWN"


@router.get("/api/audit/scan/{run_id}/status")
async def api_audit_scan_status(request: Request, run_id: str) -> HTMLResponse:
    """Return scan progress partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    status = await asyncio.to_thread(_get_run_status, db_path, run_id)
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
    from q_ai.services.workflow_service import (
        WorkflowValidationError,
        validate_transport_and_command,
    )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422, content={"detail": "Request body must be a JSON object"}
        )

    try:
        validate_transport_and_command(body)
    except WorkflowValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": exc.detail})
    except TypeError:
        return JSONResponse(status_code=422, content={"detail": "Invalid request parameters"})

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


def _list_findings(db_path: Path | None, run_id: str) -> list:
    """Load findings for a run (blocking SQLite)."""
    with get_connection(db_path) as conn:
        return finding_service.list_findings(conn, run_id=run_id)


@router.get("/api/audit/findings/{run_id}")
async def api_audit_findings(request: Request, run_id: str) -> HTMLResponse:
    """Return findings partial for a specific scan run."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    findings = await asyncio.to_thread(_list_findings, db_path, run_id)
    return templates.TemplateResponse(
        request, "partials/audit_findings.html", {"findings": findings}
    )
