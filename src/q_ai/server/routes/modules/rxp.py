"""RXP module routes — tab, validations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import _get_db_path, _get_templates

router = APIRouter()


@router.get("/api/rxp/tab")
async def api_rxp_tab(request: Request) -> HTMLResponse:
    """Return the RXP tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/rxp_tab.html", {})


def _load_validations(db_path: Path | None) -> dict[str, Any]:
    """Load RXP validation rows and aggregate stats (blocking SQLite)."""
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
    return {
        "validations": [dict(row) for row in rows],
        "total_validations": total_row[0] if total_row else 0,
        "models_tested": models_row[0] if models_row else 0,
        "avg_retrieval_rate": (avg_row[0] if avg_row and avg_row[0] is not None else 0.0),
    }


@router.get("/api/rxp/validations")
async def api_rxp_validations(request: Request) -> HTMLResponse:
    """Return RXP validations with stats."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    data = await asyncio.to_thread(_load_validations, db_path)
    return templates.TemplateResponse(
        request,
        "partials/rxp_tab.html",
        data,
    )
