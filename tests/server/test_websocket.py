"""Tests for WebSocket endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_LOCAL_ORIGIN = {"origin": "http://127.0.0.1:8000"}


class TestWebSocket:
    """WebSocket /ws endpoint accepts connections."""

    def test_connects_successfully(self, client: TestClient) -> None:
        """The /ws endpoint accepts a WebSocket connection."""
        with client.websocket_connect("/ws", headers=_LOCAL_ORIGIN) as ws:
            ws.send_text("ping")

    def test_receives_broadcast(self, client: TestClient) -> None:
        """Connected client receives events broadcast via ws_manager."""
        import asyncio

        manager = client.app.state.ws_manager

        with client.websocket_connect("/ws", headers=_LOCAL_ORIGIN) as ws:
            event = {"type": "run_status", "run_id": "r1", "status": 1, "module": "audit"}
            # broadcast is async — run it in the event loop
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(manager.broadcast(event))
            finally:
                loop.close()
            data = ws.receive_json()
            assert data["type"] == "run_status"
            assert data["run_id"] == "r1"
            assert data["status"] == 1

    def test_handles_disconnect(self, client: TestClient) -> None:
        """After disconnect, the connection is removed from the manager."""
        manager = client.app.state.ws_manager
        with client.websocket_connect("/ws", headers=_LOCAL_ORIGIN):
            assert manager.active_connections >= 1
        # After context exit, connection should be cleaned up
        assert manager.active_connections == 0


# ---------------------------------------------------------------------------
# WA1 — CSWSH Origin validation on /ws and /ws/assist
# ---------------------------------------------------------------------------

_ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://localhost",
]
_DISALLOWED_ORIGINS = [
    "https://evil.example",
    "https://127.0.0.1:8000",  # https scheme disallowed
    "http://127.0.0.1.evil.com:8000",  # prefix-match evasion
    "http://example.com",
    "null",
    "",
]


@pytest.mark.parametrize("endpoint", ["/ws", "/ws/assist"])
@pytest.mark.parametrize("origin", _ALLOWED_ORIGINS)
def test_allowed_origin_connects(client: TestClient, endpoint: str, origin: str) -> None:
    """Localhost origins connect successfully on both WebSocket endpoints."""
    with client.websocket_connect(endpoint, headers={"origin": origin}) as ws:
        # Connection established — on /ws send a ping; on /ws/assist send reset.
        if endpoint == "/ws":
            ws.send_text("ping")
        else:
            ws.send_json({"type": "assist_reset"})
            resp = ws.receive_json()
            assert resp["type"] == "assist_reset_done"


@pytest.mark.parametrize("endpoint", ["/ws", "/ws/assist"])
@pytest.mark.parametrize("origin", _DISALLOWED_ORIGINS)
def test_disallowed_origin_rejected(client: TestClient, endpoint: str, origin: str) -> None:
    """Disallowed origins close with 1008 before any frame exchange."""
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(endpoint, headers={"origin": origin}) as ws,
    ):
        # Accept should never have fired — receive raises WebSocketDisconnect.
        ws.receive_text()
    assert exc_info.value.code == 1008


@pytest.mark.parametrize("endpoint", ["/ws", "/ws/assist"])
def test_missing_origin_rejected(client: TestClient, endpoint: str) -> None:
    """Missing Origin header is rejected the same as disallowed origins.

    Browsers always send Origin for cross-site connections, so a missing
    header is either a non-browser client (not this tool's use case) or
    an attacker stripping the header. TestClient does not set Origin by
    default for ``websocket_connect``, so omitting it here yields a request
    with no Origin header.
    """
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(endpoint) as ws,
    ):
        ws.receive_text()
    assert exc_info.value.code == 1008
