"""Tests for assistant web UI: chat page, WebSocket, nav, and assist panel."""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from q_ai.core.db import create_run, create_target
from q_ai.core.models import RunStatus

# ---------------------------------------------------------------------------
# Lightweight stub for q_ai.assist.service so WebSocket tests don't pull in
# the heavy torch/transformers/sentence-transformers import chain.
# ---------------------------------------------------------------------------


class _StubNotConfiguredError(Exception):
    """Stand-in for AssistantNotConfiguredError."""


def _ensure_assist_service_stub() -> types.ModuleType:
    """Return (and cache in sys.modules) a lightweight stub module.

    Prevents the heavy torch/transformers import chain from being triggered
    when the WebSocket handler does ``from q_ai.assist.service import ...``.
    """
    mod = sys.modules.get("q_ai.assist.service")
    if mod is not None:
        return mod

    mod = types.ModuleType("q_ai.assist.service")
    mod.AssistantNotConfiguredError = _StubNotConfiguredError  # type: ignore[attr-defined]

    async def _noop_stream(*a: object, **kw: object) -> object:  # type: ignore[misc]
        yield ""

    mod.chat_stream = _noop_stream  # type: ignore[attr-defined]
    mod.chat = AsyncMock(return_value="stub")  # type: ignore[attr-defined]

    # Set on the parent package if it already exists
    parent = sys.modules.get("q_ai.assist")
    if parent is not None:
        parent.service = mod  # type: ignore[attr-defined]

    sys.modules["q_ai.assist.service"] = mod
    return mod


class TestAssistPageRoute:
    """GET / returns the assistant chat page."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_contains_assistant_title(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "Assistant" in resp.text

    def test_unconfigured_shows_setup_card(self, client: TestClient) -> None:
        """When assist provider/model not set, shows setup card."""
        resp = client.get("/")
        assert "Configure Assistant" in resp.text
        assert "Save &amp; Start" in resp.text

    def test_configured_shows_chat_interface(self, client: TestClient, tmp_db: Path) -> None:
        """When assist provider/model are set, shows chat interface."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.provider", "ollama"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.model", "llama3.1"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/")
        assert resp.status_code == 200
        assert "assist-chat-messages" in resp.text
        assert "assist-input" in resp.text
        assert "Configure Assistant" not in resp.text

    def test_new_user_prompts(self, client: TestClient, tmp_db: Path) -> None:
        """Chat page shows new-user prompts when no runs exist."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.provider", "ollama"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.model", "llama3.1"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/")
        assert "What can qai test for?" in resp.text
        assert "How do I scan an MCP server?" in resp.text

    def test_active_user_prompts(self, client: TestClient, tmp_db: Path) -> None:
        """Chat page shows active-user prompts when runs exist."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.provider", "ollama"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.model", "llama3.1"),
            )
            create_run(conn, module="audit", name="test-run")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/")
        assert "Summarize my recent findings" in resp.text
        assert "What should I test next?" in resp.text


class TestLauncherRouteChange:
    """GET /launcher returns the launcher page."""

    def test_launcher_returns_200(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        assert resp.status_code == 200

    def test_launcher_contains_workflows(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        assert "Assess an MCP Server" in resp.text
        assert "wf-panel" in resp.text


class TestNavigation:
    """Navigation links are correct across pages."""

    def test_assist_page_nav(self, client: TestClient) -> None:
        """Assistant page has correct nav with active state."""
        resp = client.get("/")
        assert 'href="/"' in resp.text
        assert 'href="/launcher"' in resp.text
        assert 'href="/runs"' in resp.text
        assert "Assistant" in resp.text

    def test_launcher_page_nav(self, client: TestClient) -> None:
        """Launcher page has correct nav with active state."""
        resp = client.get("/launcher")
        assert 'href="/"' in resp.text
        assert 'href="/launcher"' in resp.text

    def test_assist_active_state(self, client: TestClient) -> None:
        """Assistant nav link is active on assist page."""
        resp = client.get("/")
        # The nav link for assistant should have active class
        assert "Assistant" in resp.text

    def test_logo_links_to_root(self, client: TestClient) -> None:
        """The {q-AI} logo links to / (assistant page)."""
        resp = client.get("/launcher")
        # Logo is an anchor tag linking to /
        assert '<a href="/' in resp.text
        # Logo contains the brand text in spans
        assert "q-AI" in resp.text or "q</span>" in resp.text


class TestAssistWebSocket:
    """WebSocket /ws/assist endpoint basics."""

    def test_ws_assist_connects(self, client: TestClient) -> None:
        """WebSocket endpoint accepts connections."""
        with client.websocket_connect("/ws/assist") as ws:
            # Send empty query — should get error back
            ws.send_json({"type": "assist_query", "message": ""})
            data = ws.receive_json()
            assert data["type"] == "assist_error"
            assert "Empty message" in data["message"]

    def test_ws_assist_invalid_json(self, client: TestClient) -> None:
        """Invalid JSON returns error message."""
        with client.websocket_connect("/ws/assist") as ws:
            ws.send_text("not json")
            data = ws.receive_json()
            assert data["type"] == "assist_error"
            assert "Invalid JSON" in data["message"]

    def test_ws_assist_ignores_unknown_type(self, client: TestClient) -> None:
        """Messages with unknown type are silently ignored."""
        with client.websocket_connect("/ws/assist") as ws:
            ws.send_json({"type": "unknown_type", "message": "hello"})
            # Send a valid message after to verify connection is still alive
            ws.send_json({"type": "assist_query", "message": ""})
            data = ws.receive_json()
            assert data["type"] == "assist_error"

    def test_ws_assist_streams_tokens(self, client: TestClient) -> None:
        """Successful query streams tokens then done."""
        stub = _ensure_assist_service_stub()

        async def _gen(*args: object, **kwargs: object) -> object:
            for token in ["Hello", " ", "world"]:
                yield token

        original = stub.chat_stream  # type: ignore[union-attr]
        stub.chat_stream = _gen  # type: ignore[union-attr]
        try:
            with client.websocket_connect("/ws/assist") as ws:
                ws.send_json({"type": "assist_query", "message": "hi"})
                tokens = []
                while True:
                    data = ws.receive_json()
                    if data["type"] == "assist_token":
                        tokens.append(data["token"])
                    elif data["type"] == "assist_done":
                        break
                    elif data["type"] == "assist_error":
                        pytest.fail(f"Unexpected error: {data['message']}")
                        break
                assert tokens == ["Hello", " ", "world"]
        finally:
            stub.chat_stream = original  # type: ignore[union-attr]

    def test_ws_assist_not_configured_error(self, client: TestClient) -> None:
        """Unconfigured assistant returns error via WebSocket."""
        stub = _ensure_assist_service_stub()

        async def _fail(*args: object, **kwargs: object) -> object:
            raise _StubNotConfiguredError("Not configured")
            yield  # type: ignore[misc]  # make it an async generator

        original_stream = stub.chat_stream  # type: ignore[union-attr]
        original_exc = stub.AssistantNotConfiguredError  # type: ignore[union-attr]
        stub.chat_stream = _fail  # type: ignore[union-attr]
        stub.AssistantNotConfiguredError = _StubNotConfiguredError  # type: ignore[union-attr]
        try:
            with client.websocket_connect("/ws/assist") as ws:
                ws.send_json({"type": "assist_query", "message": "test"})
                data = ws.receive_json()
                assert data["type"] == "assist_error"
                assert "Not configured" in data["message"]
        finally:
            stub.chat_stream = original_stream  # type: ignore[union-attr]
            stub.AssistantNotConfiguredError = original_exc  # type: ignore[union-attr]


class TestAssistPanelOnRunResults:
    """Run results pages include the assist panel."""

    def test_assist_panel_on_terminal_run(self, client: TestClient, tmp_db: Path) -> None:
        """Completed run results page includes assist panel."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id = create_target(conn, type="server", name="test-srv")
            run_id = create_run(conn, module="audit", name="assess", target_id=target_id)
            conn.execute(
                "UPDATE runs SET status = ? WHERE id = ?",
                (int(RunStatus.COMPLETED), run_id),
            )
            # Configure assist
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.provider", "ollama"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.model", "llama3.1"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/runs?run_id={run_id}")
        assert resp.status_code == 200
        assert "Ask about this run" in resp.text
        assert "assist-panel" in resp.text

    def test_assist_panel_unconfigured_shows_link(self, client: TestClient, tmp_db: Path) -> None:
        """Unconfigured assist shows settings link instead of panel."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id = create_target(conn, type="server", name="test-srv2")
            run_id = create_run(conn, module="audit", name="assess", target_id=target_id)
            conn.execute(
                "UPDATE runs SET status = ? WHERE id = ?",
                (int(RunStatus.COMPLETED), run_id),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/runs?run_id={run_id}")
        assert resp.status_code == 200
        assert "Configure the assistant" in resp.text

    def test_assist_panel_has_run_prompts(self, client: TestClient, tmp_db: Path) -> None:
        """Assist panel on run results shows module-appropriate prompts."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id = create_target(conn, type="server", name="test-srv3")
            run_id = create_run(conn, module="audit", name="assess", target_id=target_id)
            conn.execute(
                "UPDATE runs SET status = ? WHERE id = ?",
                (int(RunStatus.COMPLETED), run_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.provider", "ollama"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.model", "llama3.1"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/runs?run_id={run_id}")
        assert resp.status_code == 200
        assert "Explain these findings" in resp.text


class TestSuggestedPromptsRoute:
    """Suggested prompts are contextual."""

    def test_chat_page_no_runs_gets_new_user_prompts(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        """New user (no runs) sees beginner prompts."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.provider", "ollama"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("assist.model", "llama3.1"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/")
        assert "What can qai test for?" in resp.text
        assert "Summarize my recent findings" not in resp.text
