"""Tests for the WebSocket ConnectionManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from q_ai.server.websocket import ConnectionManager


@pytest.fixture
def manager() -> ConnectionManager:
    """Create a fresh ConnectionManager."""
    return ConnectionManager()


def _mock_websocket() -> MagicMock:
    """Create a mock WebSocket with async methods."""
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


class TestConnectionManager:
    """ConnectionManager unit tests."""

    async def test_connect_disconnect(self, manager: ConnectionManager) -> None:
        """Connection tracking works correctly."""
        ws = _mock_websocket()
        await manager.connect(ws)
        assert manager.active_connections == 1
        manager.disconnect(ws)
        assert manager.active_connections == 0

    async def test_broadcast_sends_to_all(self, manager: ConnectionManager) -> None:
        """Broadcast sends event to all connected clients."""
        ws1 = _mock_websocket()
        ws2 = _mock_websocket()
        await manager.connect(ws1)
        await manager.connect(ws2)

        event = {"type": "test", "run_id": "123"}
        await manager.broadcast(event)

        ws1.send_json.assert_called_once_with(event)
        ws2.send_json.assert_called_once_with(event)

    async def test_broadcast_with_no_connections(self, manager: ConnectionManager) -> None:
        """Broadcast with no connections does not raise."""
        await manager.broadcast({"type": "test", "run_id": "123"})

    async def test_broadcast_removes_failed_connections(self, manager: ConnectionManager) -> None:
        """Broadcast removes clients that fail to receive."""
        ws_ok = _mock_websocket()
        ws_bad = _mock_websocket()
        ws_bad.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

        await manager.connect(ws_ok)
        await manager.connect(ws_bad)
        assert manager.active_connections == 2

        await manager.broadcast({"type": "test", "run_id": "123"})
        assert manager.active_connections == 1

    async def test_send_to(self, manager: ConnectionManager) -> None:
        """send_to sends event to a specific client."""
        ws = _mock_websocket()
        await manager.connect(ws)
        event = {"type": "status", "run_id": "456"}
        await manager.send_to(ws, event)
        ws.send_json.assert_called_with(event)

    async def test_disconnect_unknown_no_error(self, manager: ConnectionManager) -> None:
        """Disconnecting an unknown WebSocket does not raise."""
        ws = _mock_websocket()
        manager.disconnect(ws)  # Should not raise
        assert manager.active_connections == 0
