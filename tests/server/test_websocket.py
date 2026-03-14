"""Tests for WebSocket endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestWebSocket:
    """WebSocket /ws endpoint accepts connections."""

    def test_connects_and_receives_message(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            assert data["status"] == "connected"
