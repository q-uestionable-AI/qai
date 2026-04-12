"""Proxy module routes — sessions list and detail."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from q_ai.core.db import get_connection
from q_ai.server.routes._shared import _get_db_path, _get_templates

router = APIRouter()

_ARTIFACTS_BASE = Path.home() / ".qai" / "artifacts"


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
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM proxy_sessions WHERE run_id = ?", (run_id,)).fetchone()
    session_data: dict[str, Any] = dict(row) if row else {}

    # Load message summary from session JSON if available
    messages_summary: list[dict[str, Any]] = []
    if session_data.get("session_file"):
        artifacts_dir = _ARTIFACTS_BASE.resolve()
        session_file = session_data["session_file"]
        # Reject path traversal attempts
        session_path = (artifacts_dir / session_file).resolve()
        if not session_path.is_relative_to(artifacts_dir):
            session_path = None  # type: ignore[assignment]
        if session_path and session_path.is_file():
            try:
                raw = _json.loads(session_path.read_text(encoding="utf-8"))
            except (_json.JSONDecodeError, OSError):
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
