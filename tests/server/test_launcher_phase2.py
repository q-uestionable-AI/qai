"""Tests for Phase 2 launcher features: hero card, defaults, model options,
target name check, quick actions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestHeroCard:
    """Hero workflow card renders at the top of the launcher grid."""

    def test_hero_card_present(self, client: TestClient) -> None:
        """Launcher page contains the hero card with wf-hero class."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "wf-hero" in resp.text

    def test_hero_is_assess(self, client: TestClient) -> None:
        """The hero card is the Assess workflow."""
        resp = client.get("/")
        assert "Assess an MCP Server" in resp.text

    def test_non_hero_workflows_in_grid(self, client: TestClient) -> None:
        """Non-hero workflows still render in the grid."""
        resp = client.get("/")
        assert "Test Document Ingestion" in resp.text
        assert "Trace an Attack Path" in resp.text

    def test_autofit_grid_class(self, client: TestClient) -> None:
        """Grid container uses auto-fit CSS class."""
        resp = client.get("/")
        assert "wf-grid" in resp.text


class TestLauncherDefaults:
    """Launcher reads and applies settings defaults."""

    def test_defaults_in_context(self, client: TestClient, tmp_db: Path) -> None:
        """Launcher includes default_model and audit_default_transport."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("default_model", "lmstudio/qwen2.5-7b"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("audit.default_transport", "sse"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("lmstudio.base_url", "http://localhost:1234"),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/")
        assert resp.status_code == 200
        # Model should appear as an option (lmstudio is configured via base_url)
        assert "lmstudio/qwen2.5-7b" in resp.text


class TestModelOptions:
    """Model dropdown shows actual model names from settings."""

    def test_default_model_shown(self, client: TestClient, tmp_db: Path) -> None:
        """When default_model is set, it appears in the dropdown."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("default_model", "openai/gpt-4o"),
            )
            conn.commit()
        finally:
            conn.close()

        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.get("/")
        assert "openai/gpt-4o" in resp.text

    def test_fallback_to_provider_default(self, client: TestClient) -> None:
        """Without default_model, providers show provider/default."""
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.get("/")
        assert resp.status_code == 200
        # All providers should render as provider/default in the dropdown
        assert "anthropic/default" in resp.text

    def test_local_providers_included_without_config(self, client: TestClient) -> None:
        """ollama/lmstudio appear in the dropdown even without explicit config."""
        resp = client.get("/")
        assert resp.status_code == 200
        # These have default URLs and should be available
        assert "ollama/default" in resp.text
        assert "lmstudio/default" in resp.text


class TestUrlPlaceholder:
    """URL placeholder text is present in the template."""

    def test_sse_placeholder_in_template(self, client: TestClient) -> None:
        """SSE placeholder appears in the template source."""
        resp = client.get("/")
        assert "http://localhost:3000/sse" in resp.text

    def test_streamable_http_placeholder_in_js(self, client: TestClient) -> None:
        """Streamable-http placeholder appears in the JS constants."""
        resp = client.get("/")
        assert "http://localhost:3000/mcp" in resp.text


class TestTargetNameCheck:
    """GET /api/targets/check-name returns existence status."""

    def test_nonexistent_name(self, client: TestClient) -> None:
        """Unknown target name returns exists=false."""
        resp = client.get("/api/targets/check-name?name=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == {"exists": False}

    def test_existing_name(self, client: TestClient, tmp_db: Path) -> None:
        """Existing target name returns exists=true."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT INTO targets (id, type, name, created_at) "
                "VALUES ('t1', 'server', 'my-server', datetime('now'))"
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/targets/check-name?name=my-server")
        assert resp.status_code == 200
        assert resp.json() == {"exists": True}

    def test_check_name_requires_param(self, client: TestClient) -> None:
        """Missing name parameter returns 422."""
        resp = client.get("/api/targets/check-name")
        assert resp.status_code == 422


class TestQuickActionsSection:
    """Quick Actions section appears in the launcher."""

    def test_quick_actions_present(self, client: TestClient) -> None:
        """Launcher page contains the Quick Actions heading."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Quick Actions" in resp.text

    def test_launch_has_inflight_guard(self, client: TestClient) -> None:
        """Launch JS includes in-flight duplicate-submit guard."""
        resp = client.get("/")
        assert "_launchInFlight" in resp.text

    def test_quick_action_buttons(self, client: TestClient) -> None:
        """Quick Action buttons for Scan, Intercept, Campaign are present."""
        resp = client.get("/")
        assert "qa_scan" in resp.text
        assert "qa_intercept" in resp.text
        assert "qa_campaign" in resp.text


class TestQuickActionLaunch:
    """POST /api/quick-actions/launch validates and creates runs."""

    def test_invalid_json_rejected(self, client: TestClient) -> None:
        """Malformed JSON body returns 400."""
        resp = client.post(
            "/api/quick-actions/launch",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]

    def test_non_string_field_rejected(self, client: TestClient) -> None:
        """Non-string value for action field returns 422."""
        resp = client.post(
            "/api/quick-actions/launch",
            json={
                "action": 123,
                "target_name": "x",
                "transport": "stdio",
                "command": "echo hi",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Invalid request parameters"

    def test_non_object_body_rejected(self, client: TestClient) -> None:
        """Non-object JSON body (e.g. array) returns 422."""
        resp = client.post(
            "/api/quick-actions/launch",
            json=[1, 2, 3],
        )
        assert resp.status_code == 422
        assert "JSON object" in resp.json()["detail"]

    def test_unknown_action_rejected(self, client: TestClient) -> None:
        """Unknown action returns 422."""
        resp = client.post(
            "/api/quick-actions/launch",
            json={"action": "invalid", "target_name": "x", "transport": "stdio"},
        )
        assert resp.status_code == 422
        assert "Unknown action" in resp.json()["detail"]

    def test_missing_target_name(self, client: TestClient) -> None:
        """Missing target_name returns 422."""
        resp = client.post(
            "/api/quick-actions/launch",
            json={"action": "scan", "transport": "stdio", "command": "echo hi"},
        )
        assert resp.status_code == 422
        assert "target_name" in resp.json()["detail"]

    def test_invalid_transport(self, client: TestClient) -> None:
        """Invalid transport returns 422."""
        resp = client.post(
            "/api/quick-actions/launch",
            json={
                "action": "scan",
                "target_name": "x",
                "transport": "invalid",
                "command": "echo hi",
            },
        )
        assert resp.status_code == 422

    def test_scan_launch_creates_run(self, client: TestClient, tmp_db: Path) -> None:
        """Launching a scan quick action creates a run and returns redirect."""
        with patch("q_ai.server.routes._run_workflow", new_callable=AsyncMock):
            resp = client.post(
                "/api/quick-actions/launch",
                json={
                    "action": "scan",
                    "target_name": "qa-test",
                    "transport": "stdio",
                    "command": "echo hi",
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert "run_id" in data
        assert "redirect" in data

        # Verify target was created
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM targets WHERE name = ?", ("qa-test",)).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_campaign_requires_model(self, client: TestClient) -> None:
        """Campaign action without model returns 422."""
        resp = client.post(
            "/api/quick-actions/launch",
            json={
                "action": "campaign",
                "target_name": "x",
                "transport": "stdio",
                "command": "echo hi",
            },
        )
        assert resp.status_code == 422
        assert "model" in resp.json()["detail"]

    def test_campaign_rejects_invalid_rounds(self, client: TestClient) -> None:
        """Campaign with non-integer rounds returns 422."""
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.post(
                "/api/quick-actions/launch",
                json={
                    "action": "campaign",
                    "target_name": "x",
                    "transport": "stdio",
                    "command": "echo hi",
                    "model": "openai/gpt-4",
                    "rounds": "abc",
                },
            )
        assert resp.status_code == 422
        assert "rounds" in resp.json()["detail"]

    def test_campaign_rejects_out_of_range_rounds(self, client: TestClient) -> None:
        """Campaign with rounds > 10 returns 422."""
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.post(
                "/api/quick-actions/launch",
                json={
                    "action": "campaign",
                    "target_name": "x",
                    "transport": "stdio",
                    "command": "echo hi",
                    "model": "openai/gpt-4",
                    "rounds": 20,
                },
            )
        assert resp.status_code == 422
        assert "rounds" in resp.json()["detail"]

    def test_campaign_requires_provider_credential(self, client: TestClient) -> None:
        """Campaign action checks for provider credentials."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.post(
                "/api/quick-actions/launch",
                json={
                    "action": "campaign",
                    "target_name": "x",
                    "transport": "stdio",
                    "command": "echo hi",
                    "model": "openai/gpt-4",
                },
            )
        assert resp.status_code == 422
        assert "credential" in resp.json()["detail"]

    def test_intercept_launch_creates_run(self, client: TestClient, tmp_db: Path) -> None:
        """Intercept quick action creates a run and target."""
        with patch("q_ai.server.routes._run_workflow", new_callable=AsyncMock):
            resp = client.post(
                "/api/quick-actions/launch",
                json={
                    "action": "intercept",
                    "target_name": "proxy-test",
                    "transport": "stdio",
                    "command": "echo hi",
                },
            )
        assert resp.status_code == 201
        data = resp.json()
        assert "run_id" in data

    def test_campaign_launch_with_valid_credential(self, client: TestClient, tmp_db: Path) -> None:
        """Campaign with valid credential creates a run."""
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes._run_workflow", new_callable=AsyncMock),
        ):
            resp = client.post(
                "/api/quick-actions/launch",
                json={
                    "action": "campaign",
                    "target_name": "inject-test",
                    "transport": "stdio",
                    "command": "echo hi",
                    "model": "openai/gpt-4",
                    "rounds": 1,
                },
            )
        assert resp.status_code == 201
