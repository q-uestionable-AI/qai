"""Tests for the settings API routes."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestSettingsPage:
    """Tests for the settings page rendering."""

    def test_settings_page_renders(self, client: TestClient) -> None:
        """GET /settings returns 200."""
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/settings")
        assert resp.status_code == 200


class TestAddProvider:
    """Tests for adding a provider via API."""

    def test_add_provider_writes_keyring(self, client: TestClient) -> None:
        """POST provider with api_key -> set_credential called correctly."""
        with patch("q_ai.server.routes.set_credential") as mock_set:
            resp = client.post(
                "/api/settings/providers",
                json={"provider": "openai", "api_key": "sk-test"},
            )
        assert resp.status_code == 201
        mock_set.assert_called_once_with("openai", "sk-test")


class TestDeleteProvider:
    """Tests for deleting a provider via API."""

    def test_delete_provider_clears_keyring(self, client: TestClient) -> None:
        """DELETE provider -> delete_credential called."""
        with patch("q_ai.server.routes.delete_credential") as mock_del:
            resp = client.delete("/api/settings/providers/openai")
        assert resp.status_code == 200
        mock_del.assert_called_once_with("openai")


class TestListProviders:
    """Tests for listing providers with status."""

    def test_list_providers(self, client: TestClient) -> None:
        """GET /api/settings/providers returns providers list."""

        def _mock_cred(provider: str) -> str | None:
            return "key" if provider == "openai" else None

        with patch("q_ai.server.routes.get_credential", side_effect=_mock_cred):
            resp = client.get("/api/settings/providers")

        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        providers = data["providers"]
        assert len(providers) > 0

        openai_entry = next(p for p in providers if p["name"] == "openai")
        assert openai_entry["configured"] is True
        assert openai_entry["has_key"] is True


class TestSaveDefaults:
    """Tests for saving and retrieving default settings."""

    def test_save_defaults(self, client: TestClient) -> None:
        """POST defaults -> GET defaults returns saved values."""
        resp = client.post(
            "/api/settings/defaults",
            json={
                "audit.default_transport": "sse",
                "ipi.default_callback_url": "http://10.0.0.5:8080/callback",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/settings/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert data["audit.default_transport"] == "sse"
        assert data["ipi.default_callback_url"] == "http://10.0.0.5:8080/callback"

    def test_no_provider_model_in_defaults(self, client: TestClient) -> None:
        """Defaults API does not include provider/model keys."""
        resp = client.get("/api/settings/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert "default_provider" not in data
        assert "default_model_id" not in data


class TestProvidersInsecureKeyring:
    """Tests for provider status when keyring is unavailable."""

    def test_providers_status_insecure_keyring(self, client: TestClient) -> None:
        """GET /api/settings/providers returns 200 even when keyring raises."""
        with patch(
            "q_ai.server.routes.get_credential", side_effect=RuntimeError("insecure backend")
        ):
            resp = client.get("/api/settings/providers")

        assert resp.status_code == 200
        data = resp.json()
        providers = data["providers"]
        assert len(providers) == 7
        for p in providers:
            assert p["has_key"] is False
            assert p["keyring_unavailable"] is True
