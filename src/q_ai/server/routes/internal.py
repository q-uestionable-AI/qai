"""Internal bridge endpoint — token-authenticated IPI hit callback."""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import _get_db_path

router = APIRouter()


@router.post("/api/internal/ipi-hit")
async def api_internal_ipi_hit(request: Request) -> JSONResponse:
    """Receive a hit notification from the IPI callback server.

    Validates the bridge token (cached at app startup), reads the
    canonical hit from the DB, and broadcasts an ``ipi_hit`` WebSocket
    event. Non-creating: never writes or mutates hit records.

    Args:
        request: The incoming FastAPI request. Must include an
            ``X-QAI-Bridge-Token`` header matching the cached token
            and a JSON body with ``{"hit_id": "<id>"}``.

    Returns:
        JSONResponse with ``{"status": "ok"}`` on success, 401 if the
        bridge token is missing or invalid, 400 if the body is malformed,
        or 404 if the hit ID does not exist in the database.
    """
    token = request.headers.get("X-QAI-Bridge-Token")
    expected: str | None = request.app.state.bridge_token
    if not token or not expected or token != expected:
        return JSONResponse(status_code=401, content={"detail": "Invalid bridge token"})

    try:
        body = await request.json()
    except (ValueError, _json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "Expected JSON object"})

    hit_id = body.get("hit_id")
    if not hit_id:
        return JSONResponse(status_code=400, content={"detail": "Missing hit_id"})

    db_path = _get_db_path(request)

    def _read_hit() -> dict[str, Any] | None:
        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id, uuid, source_ip, user_agent, confidence,"
                " token_valid, via_tunnel, timestamp, body"
                " FROM ipi_hits WHERE id = ?",
                (hit_id,),
            ).fetchone()
            return dict(row) if row else None

    hit_data = await asyncio.to_thread(_read_hit)
    if not hit_data:
        return JSONResponse(status_code=404, content={"detail": "Hit not found"})

    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast({"type": "ipi_hit", **hit_data})
    return JSONResponse(content={"status": "ok"})
