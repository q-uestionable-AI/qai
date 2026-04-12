"""IPI module routes — tab, campaigns."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import _get_db_path, _get_templates

router = APIRouter()


@router.get("/api/ipi/tab")
async def api_ipi_tab(request: Request) -> HTMLResponse:
    """Return the IPI tab partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(request, "partials/ipi_tab.html", {})


def _load_campaigns(db_path: Path | None) -> dict[str, Any]:
    """Load campaign summary data (blocking SQLite reads)."""
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
    return {
        "campaigns": [dict(row) for row in rows],
        "total_hits": total_hits_row[0] if total_hits_row else 0,
        "high_hits": high_hits_row[0] if high_hits_row else 0,
    }


@router.get("/api/ipi/campaigns")
async def api_ipi_campaigns(request: Request) -> HTMLResponse:
    """Return IPI campaigns summary partial."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    data = await asyncio.to_thread(_load_campaigns, db_path)
    return templates.TemplateResponse(
        request,
        "partials/ipi_tab.html",
        {
            "campaigns": data["campaigns"],
            "total_hits": data["total_hits"],
            "high_hits": data["high_hits"],
            "listener_hint": True,
        },
    )
