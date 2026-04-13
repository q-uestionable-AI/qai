"""Chain module routes — tab, executions list/detail, templates."""

from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path
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


def _load_executions(db_path: Path | None) -> list[dict[str, Any]]:
    """Load recent chain executions (blocking SQLite)."""
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
    return [dict(row) for row in rows]


@router.get("/api/chain/executions")
async def api_chain_executions(request: Request) -> HTMLResponse:
    """Return chain executions list partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    executions = await asyncio.to_thread(_load_executions, db_path)
    return templates.TemplateResponse(
        request, "partials/chain_tab.html", {"executions": executions}
    )


def _safe_json_dict(raw: Any, context: str) -> dict[str, Any] | None:
    """Parse ``raw`` as a JSON object, returning None (and logging) on failure."""
    if not isinstance(raw, str):
        if raw is not None:
            logger.debug("%s: expected JSON string, got %s", context, type(raw).__name__)
        return None
    try:
        parsed = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        logger.debug("%s: JSON parse failed", context)
        return None
    if not isinstance(parsed, dict):
        logger.debug("%s: parsed JSON is not an object", context)
        return None
    return parsed


def _load_guidance_dict(raw_artifacts: Any) -> dict[str, Any] | None:
    """Extract and parse the ``guidance`` JSON string from a step's artifacts.

    Returns ``None`` whenever the data shape is unexpected — failures are
    logged at debug level rather than propagated.
    """
    if not raw_artifacts:
        return None
    parsed = _safe_json_dict(raw_artifacts, "Chain step artifacts")
    if parsed is None:
        return None
    return _safe_json_dict(parsed.get("guidance"), "Chain step guidance")


def _parse_step_guidance(raw_artifacts: Any) -> RunGuidance | None:
    """Safely parse guidance from a step's artifacts column."""
    guidance_dict = _load_guidance_dict(raw_artifacts)
    if guidance_dict is None:
        return None
    try:
        return RunGuidance.from_dict(guidance_dict)
    except (ValueError, TypeError, KeyError):
        logger.debug("Chain step guidance failed RunGuidance validation")
        return None


def _load_execution_detail(db_path: Path | None, run_id: str) -> dict[str, Any]:
    """Load a chain execution row and its step outputs (blocking SQLite)."""
    with get_connection(db_path) as conn:
        exec_row = conn.execute(
            "SELECT * FROM chain_executions WHERE run_id = ?", (run_id,)
        ).fetchone()
        step_rows: list[Any] = []
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
        step["guidance"] = _parse_step_guidance(step.get("artifacts"))
        step_outputs.append(step)
    return {"execution_detail": execution_detail, "step_outputs": step_outputs}


@router.get("/api/chain/executions/{run_id}")
async def api_chain_execution_detail(request: Request, run_id: str) -> HTMLResponse:
    """Return chain execution detail partial with step outputs."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    ctx = await asyncio.to_thread(_load_execution_detail, db_path, run_id)
    return templates.TemplateResponse(
        request,
        "partials/chain_tab.html",
        ctx,
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


def _load_recent_executions(db_path: Path | None) -> list[dict[str, Any]]:
    """Load recent successful chain executions (blocking SQLite)."""
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
    return [dict(r) for r in rows]


@router.get("/api/chain/executions/recent")
async def api_chain_executions_recent(request: Request) -> JSONResponse:
    """Return recent successful chain executions for the blast-radius selector."""
    db_path = _get_db_path(request)
    executions = await asyncio.to_thread(_load_recent_executions, db_path)
    return JSONResponse(content={"executions": executions})
