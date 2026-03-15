"""WebSocket connection manager for event broadcasting."""

from __future__ import annotations

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for event broadcasting.

    Tracks connected WebSocket clients and broadcasts JSON events to all of them.
    Handles disconnections gracefully during broadcast.
    """

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and track a new WebSocket connection.

        Args:
            websocket: The WebSocket to accept and track.
        """
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the tracked connections.

        Args:
            websocket: The WebSocket to remove.
        """
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, event: dict) -> None:
        """Send a JSON event to all connected clients.

        Silently removes clients that fail to receive. Safe to call with no
        connections.

        Args:
            event: JSON-serializable dict to broadcast.
        """
        disconnected: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(event)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_to(self, websocket: WebSocket, event: dict) -> None:
        """Send a JSON event to a specific client.

        Args:
            websocket: The target WebSocket.
            event: JSON-serializable dict to send.
        """
        await websocket.send_json(event)

    @property
    def active_connections(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)
