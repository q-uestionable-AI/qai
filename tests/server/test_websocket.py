"""Tests for WebSocket endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestWebSocket:
    """WebSocket /ws endpoint accepts connections."""

    def test_connects_successfully(self, client: TestClient) -> None:
        """The /ws endpoint accepts a WebSocket connection."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text("ping")
