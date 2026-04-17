"""IPI module routes — tab, campaigns, managed listener."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import _get_db_path, _get_templates
from q_ai.services.managed_listener import (
    ManagedListenerConflictError,
    ManagedListenerStartupError,
    ManagedListenerStuckStopError,
    start_managed_listener,
    stop_managed_listener,
)

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


# ---------------------------------------------------------------------------
# Managed listener endpoints
# ---------------------------------------------------------------------------


async def _extract_listener_id(request: Request) -> str | None:
    """Pull a string ``listener_id`` out of the request body.

    Accepts either a JSON body (``{"listener_id": "..."}``) or a
    form-encoded body (HTMX's default with ``hx-vals``). Returns
    ``None`` on any parse failure or missing/empty value.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body: Any = await request.json()
        else:
            form = await request.form()
            body = dict(form)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    value = body.get("listener_id")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


@router.post("/api/ipi/managed-listener/start")
async def api_ipi_managed_listener_start(request: Request) -> Response:
    """Spawn a managed tunneled IPI listener.

    Uses fixed defaults (provider=cloudflare, host=127.0.0.1, port=8080)
    matching the CLI. Researchers who need other tunnel settings use
    ``qai ipi listen`` directly.

    Status codes:

    - ``200`` with an HTMX partial on success.
    - ``409`` (conflict) if another tunneled listener is already active.
    - ``502`` if the subprocess failed to start or publish its state.
    """
    templates = _get_templates(request)
    registry = request.app.state.managed_listeners
    qai_dir = getattr(request.app.state, "qai_dir", None)

    try:
        handle = await asyncio.to_thread(
            start_managed_listener,
            registry,
            qai_dir=qai_dir,
        )
    except ManagedListenerConflictError as err:
        return JSONResponse(status_code=409, content={"detail": err.detail})
    except ManagedListenerStartupError as err:
        return JSONResponse(status_code=502, content={"detail": err.detail})

    return templates.TemplateResponse(
        request,
        "partials/ipi_tunnel_badge.html",
        {"handle": handle},
    )


@router.post("/api/ipi/managed-listener/stop")
async def api_ipi_managed_listener_stop(request: Request) -> Response:
    """Stop a managed tunneled IPI listener.

    Status codes:

    - ``204`` on success or when ``listener_id`` refers to an unknown
      or already-stopped listener (idempotent per RFC Decision 1).
    - ``422`` if ``listener_id`` is missing, empty, or non-string.
    - ``500`` if the listener is stuck and could not be reaped within
      the hard ceiling (operator must intervene manually).
    """
    listener_id = await _extract_listener_id(request)
    if listener_id is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "'listener_id' must be a non-empty string"},
        )

    registry = request.app.state.managed_listeners
    qai_dir = getattr(request.app.state, "qai_dir", None)

    try:
        await asyncio.to_thread(
            stop_managed_listener,
            registry,
            listener_id,
            qai_dir=qai_dir,
        )
    except ManagedListenerStuckStopError as err:
        return JSONResponse(status_code=500, content={"detail": err.detail})

    return Response(status_code=204)


@router.get("/api/ipi/managed-listener")
async def api_ipi_managed_listener_status(request: Request) -> HTMLResponse:
    """Render the managed-listener panel partial for the IPI tab.

    Emits one card per entry in ``app.state.managed_listeners`` and,
    if populated, a read-only card for ``app.state.foreign_listener``.
    The IPI tab polls this endpoint via HTMX to refresh state.
    """
    templates = _get_templates(request)
    registry: dict[str, Any] = request.app.state.managed_listeners
    foreign = request.app.state.foreign_listener
    return templates.TemplateResponse(
        request,
        "partials/ipi_managed_listener.html",
        {"handles": list(registry.values()), "foreign": foreign},
    )
