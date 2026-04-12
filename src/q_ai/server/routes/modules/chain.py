"""Chain module routes — tab, executions list/detail, templates."""

from __future__ import annotations

import json as _json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from q_ai.core.db import get_connection
from q_ai.core.guidance import RunGuidance
from q_ai.server.routes._shared import (
    _get_db_path,
    _get_templates,
    logger,
)

router = APIRouter()


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
