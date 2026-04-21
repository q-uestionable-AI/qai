"""Shared utilities for the ``routes`` package.

All helpers that reach into ``request.app.state`` or that are imported by
multiple route modules live here. Every sub-router in ``routes/`` imports
from this module.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request, WebSocket
from fastapi.templating import Jinja2Templates

from q_ai.core.models import RunStatus

logger = logging.getLogger("q_ai.server.routes")

# WebSocket origin allowlist (WA1, CSWSH). The tool is a local web UI; any
# non-localhost origin initiating a WebSocket is either a misconfiguration
# or an attempted cross-site WebSocket hijack. Only http:// is accepted —
# the bundled UI does not run under TLS.
_ALLOWED_WS_HOSTS = frozenset({"127.0.0.1", "localhost"})
_ALLOWED_WS_SCHEME = "http"


def _is_allowed_ws_origin(origin: str | None) -> bool:
    """Return True when ``origin`` is an accepted WebSocket Origin header.

    Accepts ``http://127.0.0.1[:port]`` and ``http://localhost[:port]``.
    Rejects a missing / empty value, any non-http scheme, and any host
    that is not one of the two literals (``http://127.0.0.1.evil.com``
    is rejected because the hostname is not ``127.0.0.1``).

    Args:
        origin: Raw value of the ``Origin`` request header, or ``None``
            when the header is absent.

    Returns:
        True if the origin is allowed, False otherwise.
    """
    if not origin:
        return False
    parsed = urlsplit(origin)
    if parsed.scheme != _ALLOWED_WS_SCHEME:
        return False
    return parsed.hostname in _ALLOWED_WS_HOSTS


async def reject_unless_local_origin(websocket: WebSocket) -> bool:
    """Close the WebSocket with code 1008 if the Origin header is not allowed.

    Must be called before ``websocket.accept()``. Returns True when the
    caller should continue (origin allowed); returns False after issuing
    the close so the caller can short-circuit.

    Args:
        websocket: The incoming WebSocket (in CONNECTING state).

    Returns:
        True if the connection should proceed, False if already closed.
    """
    origin = websocket.headers.get("origin")
    if _is_allowed_ws_origin(origin):
        return True
    logger.info(
        "Rejecting WebSocket connection: origin=%r path=%s",
        origin,
        websocket.url.path,
    )
    await websocket.close(code=1008)
    return False


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
