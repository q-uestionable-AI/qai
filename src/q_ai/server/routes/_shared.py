"""Shared utilities for the ``routes`` package.

All helpers that reach into ``request.app.state`` or that are imported by
multiple route modules live here. Every sub-router in ``routes/`` imports
from this module.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from q_ai.core.models import RunStatus

logger = logging.getLogger("q_ai.server.routes")


def _get_templates(request: Request) -> Jinja2Templates:
    """Get the Jinja2Templates instance from app state."""
    result: Jinja2Templates = request.app.state.templates
    return result


def _get_db_path(request: Request) -> Path | None:
    """Get the database path from app state."""
    result: Path | None = request.app.state.db_path
    return result


def _detect_local_ip() -> str:
    """Detect the local network IP address for callback URL suggestion.

    Uses a UDP socket connection to determine which interface the OS
    would route to an external address. Does not send any traffic.

    Returns:
        Local IP address string, or "127.0.0.1" on failure.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            addr: str = s.getsockname()[0]
            return addr
    except OSError:
        return "127.0.0.1"


_TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.PARTIAL,
}

_QUICK_ACTION_DISPLAY_NAMES = {
    "qa_scan": "Quick Audit Run",
    "qa_intercept": "Quick Proxy Run",
    "qa_campaign": "Quick Inject Run",
}
