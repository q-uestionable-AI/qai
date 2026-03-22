"""Tests for the internal IPI hit bridge endpoint and hit feed dedup logic."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

BRIDGE_URL = "/api/internal/ipi-hit"
VALID_TOKEN = "deadbeef" * 4  # 32 hex chars

SAMPLE_HIT: dict[str, Any] = {
    "id": "hit-001",
    "uuid": "payload-uuid-abc",
    "source_ip": "10.0.0.42",
    "user_agent": "TestAgent/1.0",
    "confidence": "high",
    "token_valid": 1,
    "timestamp": "2026-03-21T12:00:00Z",
    "body": '{"canary": "triggered"}',
}


def _insert_hit(db_path: Path) -> None:
    """Insert the sample hit row into the ipi_hits table.

    Uses a raw sqlite3 connection to avoid opening a second WAL-mode
    connection via get_connection (which also re-runs migrate). This
    prevents potential write-lock contention on Windows CI.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO ipi_hits"
            " (id, uuid, source_ip, user_agent, confidence, token_valid, timestamp, body)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                SAMPLE_HIT["id"],
                SAMPLE_HIT["uuid"],
                SAMPLE_HIT["source_ip"],
                SAMPLE_HIT["user_agent"],
                SAMPLE_HIT["confidence"],
                SAMPLE_HIT["token_valid"],
                SAMPLE_HIT["timestamp"],
                SAMPLE_HIT["body"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _set_bridge_token(client: TestClient, token: str | None = VALID_TOKEN) -> None:
    """Set the cached bridge token on the app state."""
    client.app.state.bridge_token = token


class TestInternalIpiHitEndpoint:
    """Tests for POST /api/internal/ipi-hit."""

    def test_missing_token_returns_401(self, client: TestClient) -> None:
        """Request without X-QAI-Bridge-Token header is rejected with 401."""
        _set_bridge_token(client)
        resp = client.post(BRIDGE_URL, json={"hit_id": "hit-001"})

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid bridge token"

    def test_invalid_token_returns_401(self, client: TestClient) -> None:
        """Request with an incorrect bridge token is rejected with 401."""
        _set_bridge_token(client)
        resp = client.post(
            BRIDGE_URL,
            json={"hit_id": "hit-001"},
            headers={"X-QAI-Bridge-Token": "wrong-token"},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid bridge token"

    def test_valid_token_reads_hit_and_broadcasts(self, client: TestClient, tmp_db: Path) -> None:
        """Valid request reads the hit from DB and broadcasts a WS event."""
        _insert_hit(tmp_db)
        _set_bridge_token(client)

        mock_broadcast = AsyncMock()
        client.app.state.ws_manager.broadcast = mock_broadcast

        resp = client.post(
            BRIDGE_URL,
            json={"hit_id": SAMPLE_HIT["id"]},
            headers={"X-QAI-Bridge-Token": VALID_TOKEN},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        mock_broadcast.assert_awaited_once()
        payload = mock_broadcast.call_args[0][0]
        assert payload["type"] == "ipi_hit"
        assert payload["id"] == SAMPLE_HIT["id"]
        assert payload["uuid"] == SAMPLE_HIT["uuid"]

    def test_missing_hit_id_returns_400(self, client: TestClient) -> None:
        """Request with valid token but no hit_id returns 400."""
        _set_bridge_token(client)
        resp = client.post(
            BRIDGE_URL,
            json={},
            headers={"X-QAI-Bridge-Token": VALID_TOKEN},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Missing hit_id"

    def test_hit_not_found_returns_404(self, client: TestClient) -> None:
        """Request for a hit_id that does not exist in the DB returns 404."""
        _set_bridge_token(client)
        resp = client.post(
            BRIDGE_URL,
            json={"hit_id": "nonexistent-hit"},
            headers={"X-QAI-Bridge-Token": VALID_TOKEN},
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Hit not found"


class TestHitFeedDedup:
    """Tests verifying the broadcast payload contract for client-side dedup.

    The JavaScript hit feed uses the ``id`` field to deduplicate events.
    These tests verify that the server-side broadcast payload includes the
    required keys that the client depends on.
    """

    def test_bridge_endpoint_returns_hit_data(self, client: TestClient, tmp_db: Path) -> None:
        """Broadcast payload includes id, uuid, source_ip, and confidence keys."""
        _insert_hit(tmp_db)
        _set_bridge_token(client)

        mock_broadcast = AsyncMock()
        client.app.state.ws_manager.broadcast = mock_broadcast

        resp = client.post(
            BRIDGE_URL,
            json={"hit_id": SAMPLE_HIT["id"]},
            headers={"X-QAI-Bridge-Token": VALID_TOKEN},
        )

        assert resp.status_code == 200

        mock_broadcast.assert_awaited_once()
        payload = mock_broadcast.call_args[0][0]
        # Required keys for client-side dedup and rendering
        assert payload["id"] == SAMPLE_HIT["id"]
        assert payload["uuid"] == SAMPLE_HIT["uuid"]
        assert payload["source_ip"] == SAMPLE_HIT["source_ip"]
        assert payload["confidence"] == SAMPLE_HIT["confidence"]
        # The type discriminator must always be present
        assert payload["type"] == "ipi_hit"
