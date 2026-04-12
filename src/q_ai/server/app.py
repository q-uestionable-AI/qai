"""FastAPI application factory for the q-ai web UI."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    Startup: detects runs left in ``WAITING_FOR_USER`` by a previous server
    process. Those runs cannot be resumed because the in-memory
    ``WorkflowRunner`` is gone; this records them so route handlers can
    surface them to the operator for manual conclusion.
    Shutdown: reserved for cleanup.
    """
    import datetime as _dt

    from q_ai.core.db import get_connection
    from q_ai.core.models import RunStatus
    from q_ai.services import run_service

    stranded: dict[str, tuple[str | None, _dt.datetime | None]] = {}
    with get_connection(app.state.db_path) as conn:
        waiting = run_service.list_runs(conn, status=RunStatus.WAITING_FOR_USER)
    for run in waiting:
        stranded[run.id] = (run.name, run.started_at)
    app.state.stranded_runs = stranded
    if stranded:
        logger.warning(
            "Detected %d stranded WAITING_FOR_USER run(s) from a previous server process: %s",
            len(stranded),
            ", ".join(stranded),
        )
    yield


def create_app(db_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Optional database path override. Defaults to ~/.qai/qai.db.

    Returns:
        A configured FastAPI instance with routes, templates, and static files.
    """
    app = FastAPI(
        title="q-ai",
        description="Security testing for agentic AI",
        lifespan=_lifespan,
    )

    # Store db_path in app state for route handlers
    app.state.db_path = db_path

    # WebSocket connection manager and active workflow tracking
    from q_ai.server.websocket import ConnectionManager

    app.state.ws_manager = ConnectionManager()
    app.state.active_workflows = {}  # dict[str, WorkflowRunner]
    app.state.stranded_runs = {}  # populated by _lifespan on startup

    # Cache bridge token at startup so the internal endpoint avoids
    # blocking disk I/O on every request (mirrors ipi/server.py caching).
    from q_ai.core.bridge_token import read_bridge_token

    app.state.bridge_token = read_bridge_token()

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["format_status"] = lambda s: s.replace("_", " ").title() if s else s
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from q_ai.server.routes import router

    app.include_router(router)

    return app
