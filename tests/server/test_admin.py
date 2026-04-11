"""Tests for the admin API routes."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestAdminPage:
    """Tests for the admin page rendering."""

    def test_admin_page_renders(self, client: TestClient) -> None:
        """GET /admin returns 200."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/admin")
        assert resp.status_code == 200

    def test_admin_page_has_all_assist_provider_labels(self, client: TestClient) -> None:
        """GET /admin contains all 9 provider labels in the assistant section."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/admin")
        body = resp.text
        for label in [
            "Anthropic",
            "Google",
            "OpenAI",
            "Groq",
            "OpenRouter",
            "xAI",
            "Ollama",
            "LM Studio",
            "Custom",
        ]:
            assert label in body, f"Provider label {label!r} missing from admin page"

    def test_admin_assist_display_when_configured(self, client: TestClient, tmp_db: Path) -> None:
        """When assist is configured, admin page shows provider label and model."""
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

        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/settings")
        body = resp.text
        assert "assist-display" in body
        assert "Ollama" in body
        assert "llama3.1" in body


class TestProvidersTableRendering:
    """Tests for the target providers table HTML."""

    def test_configured_provider_shows_label_in_table(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        """Configured provider row uses the friendly label, not the key."""
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                ("ollama.base_url", "http://localhost:11434"),
            )
            conn.commit()
        finally:
            conn.close()

        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/settings")
        body = resp.text
        assert 'id="row-ollama"' in body
        assert "Ollama" in body

    def test_settings_page_has_inline_edit_and_status_check(self, client: TestClient) -> None:
        """Settings page JS includes inline edit and auto-status-check functions."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/settings")
        body = resp.text
        assert "editProviderInline" in body
        assert "checkAllProviderStatuses" in body

    def test_add_provider_divider_text(self, client: TestClient) -> None:
        """Bottom form divider says 'Add Provider' not 'Add / Edit Provider'."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/settings")
        assert "Add Provider" in resp.text
        assert "Add / Edit Provider" not in resp.text


class TestAddProvider:
    """Tests for adding a provider via API."""

    def test_add_provider_writes_keyring(self, client: TestClient) -> None:
        """POST provider with api_key -> set_credential called correctly."""
        with patch("q_ai.server.routes.set_credential") as mock_set:
            resp = client.post(
                "/api/admin/providers",
                json={"provider": "openai", "api_key": "sk-test"},
            )
        assert resp.status_code == 201
        mock_set.assert_called_once_with("openai", "sk-test")


class TestDeleteProvider:
    """Tests for deleting a provider via API."""

    def test_delete_provider_clears_keyring(self, client: TestClient) -> None:
        """DELETE provider -> delete_credential called."""
        with patch("q_ai.server.routes.delete_credential") as mock_del:
            resp = client.delete("/api/admin/providers/openai")
        assert resp.status_code == 200
        mock_del.assert_called_once_with("openai")


class TestListProviders:
    """Tests for listing providers with status."""

    def test_list_providers(self, client: TestClient) -> None:
        """GET /api/admin/providers returns providers list."""

        def _mock_cred(provider: str) -> str | None:
            return "key" if provider == "openai" else None

        with patch("q_ai.server.routes.get_credential", side_effect=_mock_cred):
            resp = client.get("/api/admin/providers")

        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        providers = data["providers"]
        assert len(providers) > 0

        openai_entry = next(p for p in providers if p["name"] == "openai")
        assert openai_entry["configured"] is True
        assert openai_entry["has_key"] is True
        assert openai_entry["label"] == "OpenAI"


class TestSaveDefaults:
    """Tests for saving and retrieving default settings."""

    def test_save_defaults(self, client: TestClient) -> None:
        """POST defaults -> GET defaults returns saved values."""
        resp = client.post(
            "/api/admin/defaults",
            json={
                "audit.default_transport": "sse",
                "ipi.default_callback_url": "http://10.0.0.5:8080/callback",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/admin/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert data["audit.default_transport"] == "sse"
        assert data["ipi.default_callback_url"] == "http://10.0.0.5:8080/callback"

    def test_no_provider_model_in_defaults(self, client: TestClient) -> None:
        """Defaults API does not include provider/model keys."""
        resp = client.get("/api/admin/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert "default_provider" not in data
        assert "default_model_id" not in data


class TestProvidersInsecureKeyring:
    """Tests for provider status when keyring is unavailable."""

    def test_providers_status_insecure_keyring(self, client: TestClient) -> None:
        """GET /api/admin/providers returns 200 even when keyring raises."""
        with patch(
            "q_ai.server.routes.get_credential", side_effect=RuntimeError("insecure backend")
        ):
            resp = client.get("/api/admin/providers")

        assert resp.status_code == 200
        data = resp.json()
        providers = data["providers"]
        assert len(providers) == 9
        for p in providers:
            assert p["has_key"] is False
            assert p["keyring_unavailable"] is True


class TestAssistCredential:
    """Tests for the assistant credential endpoint."""

    def test_save_assist_credential(self, client: TestClient) -> None:
        """POST assist credential stores under namespaced keyring key."""
        with patch("q_ai.server.routes.set_credential") as mock_set:
            resp = client.post(
                "/api/admin/assist/credential",
                json={"provider": "anthropic", "api_key": "sk-assist-test"},
            )
        assert resp.status_code == 200
        mock_set.assert_called_once_with("assist.anthropic", "sk-assist-test")

    def test_save_assist_credential_missing_provider(self, client: TestClient) -> None:
        """POST assist credential without provider returns 422."""
        resp = client.post(
            "/api/admin/assist/credential",
            json={"api_key": "sk-test"},
        )
        assert resp.status_code == 422

    def test_save_assist_credential_missing_key(self, client: TestClient) -> None:
        """POST assist credential without api_key returns 422."""
        resp = client.post(
            "/api/admin/assist/credential",
            json={"provider": "anthropic"},
        )
        assert resp.status_code == 422

    def test_save_assist_credential_keyring_unavailable(self, client: TestClient) -> None:
        """POST assist credential with broken keyring returns 422."""
        with patch(
            "q_ai.server.routes.set_credential",
            side_effect=RuntimeError("insecure backend"),
        ):
            resp = client.post(
                "/api/admin/assist/credential",
                json={"provider": "openai", "api_key": "sk-test"},
            )
        assert resp.status_code == 422


class TestProviderConnectivity:
    """Tests for the provider connectivity check endpoint."""

    def test_cloud_provider_hits_models_endpoint(self, client: TestClient) -> None:
        """Cloud test with valid credential hits the models endpoint."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 200

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("q_ai.server.routes.get_credential", return_value="sk-test"),
            patch("httpx.AsyncClient", return_value=mock_http),
        ):
            resp = client.get("/api/admin/providers/openai/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["message"] == "Connected"

    def test_cloud_provider_auth_failure(self, client: TestClient) -> None:
        """Cloud test returns error on 401."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 401

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("q_ai.server.routes.get_credential", return_value="sk-bad"),
            patch("httpx.AsyncClient", return_value=mock_http),
        ):
            resp = client.get("/api/admin/providers/openai/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "Auth failed" in data["message"]

    def test_cloud_provider_no_credential(self, client: TestClient) -> None:
        """Cloud test without credential returns 404."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/api/admin/providers/openai/test")

        assert resp.status_code == 404

    def test_cloud_provider_no_models_endpoint_falls_back(self, client: TestClient) -> None:
        """Providers without models_endpoint fall back to credential check."""
        with patch("q_ai.server.routes.get_credential", return_value="sk-test"):
            resp = client.get("/api/admin/providers/groq/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["message"] == "Credential configured"
