"""Tests for WebSocket endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestWebSocket:
    """WebSocket /ws endpoint accepts connections."""

    def test_connects_successfully(self, client: TestClient) -> None:
        """The /ws endpoint accepts a WebSocket connection."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text("ping")

    def test_receives_broadcast(self, client: TestClient) -> None:
        """Connected client receives events broadcast via ws_manager."""
        import asyncio

        manager = client.app.state.ws_manager

        with client.websocket_connect("/ws") as ws:
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
        with client.websocket_connect("/ws"):
            assert manager.active_connections >= 1
        # After context exit, connection should be cleaned up
        assert manager.active_connections == 0
