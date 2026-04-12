"""CXP module routes — tab, results, trigger override."""

from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import _get_db_path, _get_templates

router = APIRouter()


@router.get("/api/cxp/tab")
async def api_cxp_tab(request: Request) -> HTMLResponse:
    """Return the CXP tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/cxp_tab.html", {})


def _load_results(db_path: Path | None) -> dict[str, Any]:
    """Load CXP results and aggregate counts (blocking SQLite)."""
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
    return {
        "results": [dict(row) for row in rows],
        "total_tests": total_row[0] if total_row else 0,
        "hit_count": hit_row[0] if hit_row else 0,
        "partial_count": partial_row[0] if partial_row else 0,
        "miss_count": miss_row[0] if miss_row else 0,
    }


@router.get("/api/cxp/results")
async def api_cxp_results(request: Request) -> HTMLResponse:
    """Return CXP test results with stats."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    data = await asyncio.to_thread(_load_results, db_path)
    return templates.TemplateResponse(
        request,
        "partials/cxp_tab.html",
        data,
    )


def _load_guidance_blocks(raw: str | None) -> tuple[dict[str, Any], list] | None:
    """Parse guidance JSON and return ``(data, blocks)`` or ``None``.

    ``None`` is returned when the raw value is missing, not a string,
    not a JSON object, or when the ``blocks`` field is not a list.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    blocks = data.get("blocks", [])
    if not isinstance(blocks, list):
        return None
    return data, blocks


def _apply_trigger_override(blocks: list, override_text: str) -> bool:
    """Mutate ``blocks`` to apply the override, returning True on success."""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("kind") != "trigger_prompts":
            continue
        metadata = block.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            block["metadata"] = metadata
        metadata["override"] = override_text
        return True
    return False


def _sync_trigger_override(db_path: Path | None, run_id: str, override_text: str) -> str:
    """Apply a trigger prompt override to a run's guidance JSON.

    Returns:
        ``"not_found"``, ``"no_guidance"``, or ``"ok"``.
    """
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT guidance FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return "not_found"
        parsed = _load_guidance_blocks(row["guidance"])
        if parsed is None:
            return "no_guidance"
        data, blocks = parsed
        if not _apply_trigger_override(blocks, override_text):
            return "no_guidance"
        conn.execute(
            "UPDATE runs SET guidance = ? WHERE id = ?",
            (_json.dumps(data), run_id),
        )
    return "ok"


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
