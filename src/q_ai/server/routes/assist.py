"""Assistant landing page + suggested-prompts helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from q_ai.core.db import get_connection, get_setting
from q_ai.server.routes._shared import (
    _get_db_path,
    _get_templates,
)
from q_ai.server.routes.admin import (
    _get_assist_provider_choices,
    _get_providers_status,
)

router = APIRouter()


_PROMPTS_NEW_USER: list[str] = [
    "What can qai test for?",
    "How do I scan an MCP server?",
    "Explain the OWASP MCP Top 10",
    "What's the difference between audit and inject?",
]

_PROMPTS_ACTIVE_USER: list[str] = [
    "Summarize my recent findings",
    "What should I test next?",
    "Which OWASP categories haven't I tested?",
    "Help me plan a chain test",
]

_PROMPTS_RUN_GENERIC: list[str] = [
    "Explain these findings",
    "How severe is this overall?",
    "What OWASP categories are affected?",
    "What should I test next?",
]

_PROMPTS_RUN_MODULE: dict[str, list[str]] = {
    "audit": ["Which findings are most critical?"],
    "inject": ["Did any injections succeed?"],
    "ipi": ["Did the target fetch the callback?"],
    "chain": ["Where did the chain find weaknesses?"],
    "cxp": ["What poisoning techniques worked?"],
}


def _get_suggested_prompts(
    conn: Any,
    page: str = "chat",
    modules: list[str] | None = None,
) -> list[str]:
    """Return contextual suggested prompts for the assistant.

    Args:
        conn: Active database connection.
        page: Either "chat" or "run_results".
        modules: Module names from the run (for run_results page).

    Returns:
        List of suggested prompt strings.
    """
    if page == "run_results":
        prompts = list(_PROMPTS_RUN_GENERIC)
        for mod in modules or []:
            prompts.extend(_PROMPTS_RUN_MODULE.get(mod, []))
        return prompts

    # Chat page: check if runs exist
    row = conn.execute("SELECT COUNT(*) AS cnt FROM runs LIMIT 1").fetchone()
    has_runs = row["cnt"] > 0 if row else False
    return list(_PROMPTS_ACTIVE_USER) if has_runs else list(_PROMPTS_NEW_USER)


def _load_assist_page_context(db_path: Path | None) -> dict[str, Any]:
    """Load assist page DB context (blocking SQLite)."""
    with get_connection(db_path) as conn:
        assist_provider = get_setting(conn, "assist.provider") or ""
        assist_model = get_setting(conn, "assist.model") or ""
        prompts = _get_suggested_prompts(conn, page="chat")
    return {
        "assist_provider": assist_provider,
        "assist_model": assist_model,
        "assist_configured": bool(assist_provider and assist_model),
        "suggested_prompts": prompts,
    }


def _load_assist_page_full(db_path: Path | None, request: Request) -> dict[str, Any]:
    """Load full assist page context including providers_status.

    Runs in a worker thread; ``_get_providers_status`` also performs
    blocking SQLite and keyring reads, so we call it here rather than
    from the async handler.
    """
    providers_status = _get_providers_status(request)
    ctx = _load_assist_page_context(db_path)
    ctx["providers_status"] = providers_status
    return ctx


@router.get("/")
async def assist_page(request: Request) -> HTMLResponse:
    """Render the assistant chat page (default landing page)."""
    templates = _get_templates(request)
    db_path = _get_db_path(request)
    ctx = await asyncio.to_thread(_load_assist_page_full, db_path, request)

    return templates.TemplateResponse(
        request,
        "assist.html",
        {
            "active": "assist",
            "assist_configured": ctx["assist_configured"],
            "assist_provider": ctx["assist_provider"],
            "assist_model": ctx["assist_model"],
            "providers_status": ctx["providers_status"],
            "assist_providers": _get_assist_provider_choices(),
            "suggested_prompts": ctx["suggested_prompts"],
        },
    )
