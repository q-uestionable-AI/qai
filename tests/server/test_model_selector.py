"""Tests for the provider models endpoint and model selector."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from q_ai.core.providers import ModelInfo, ModelListResponse


class TestProviderModelsEndpoint:
    """GET /api/providers/{name}/models returns model area HTML partial."""

    def test_cloud_provider_returns_curated_models(self, client: TestClient) -> None:
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.get("/api/providers/anthropic/models?selector_id=test")
        assert resp.status_code == 200
        assert "Claude Sonnet 4" in resp.text
        assert "select" in resp.text.lower()

    def test_unknown_provider_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/providers/nonexistent/models?selector_id=test")
        assert resp.status_code == 404

    def test_unconfigured_provider_returns_400(self, client: TestClient) -> None:
        with patch("q_ai.server.routes.get_credential", return_value=None):
            resp = client.get("/api/providers/anthropic/models?selector_id=test")
        assert resp.status_code == 400
        assert "Settings" in resp.text

    def test_local_provider_enumerated(self, client: TestClient, tmp_db: Path) -> None:
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

        mock_response = ModelListResponse(
            models=[
                ModelInfo(id="ollama/llama3.2", label="llama3.2"),
                ModelInfo(id="ollama/mistral", label="mistral"),
            ],
            supports_custom=True,
        )
        with patch(
            "q_ai.server.routes.fetch_models",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            resp = client.get("/api/providers/ollama/models?selector_id=test")
        assert resp.status_code == 200
        assert "llama3.2" in resp.text
        assert "mistral" in resp.text

    def test_empty_model_list_shows_message(self, client: TestClient, tmp_db: Path) -> None:
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

        mock_response = ModelListResponse(
            models=[],
            supports_custom=True,
            message="No models loaded in Ollama. Pull a model and refresh.",
        )
        with patch(
            "q_ai.server.routes.fetch_models",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            resp = client.get("/api/providers/ollama/models?selector_id=test")
        assert resp.status_code == 200
        assert "No models loaded" in resp.text

    def test_unreachable_shows_error(self, client: TestClient, tmp_db: Path) -> None:
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

        mock_response = ModelListResponse(
            models=[],
            supports_custom=True,
            error="Could not connect to Ollama at http://localhost:11434",
        )
        with patch(
            "q_ai.server.routes.fetch_models",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            resp = client.get("/api/providers/ollama/models?selector_id=test")
        assert resp.status_code == 200
        assert "Could not connect" in resp.text
        assert "Settings" in resp.text

    def test_default_model_preselected(self, client: TestClient) -> None:
        with patch("q_ai.server.routes.get_credential", return_value="test-key"):
            resp = client.get(
                "/api/providers/anthropic/models?selector_id=test"
                "&default=anthropic/claude-sonnet-4-20250514"
            )
        assert resp.status_code == 200
        assert "selected" in resp.text


def _mock_workflow_entry(wf_id: str, *, requires_provider: bool = True) -> MagicMock:
    """Create a mock WorkflowEntry for launch validation tests."""
    entry = MagicMock()
    entry.id = wf_id
    entry.executor = AsyncMock()
    entry.requires_provider = requires_provider
    return entry


class TestLaunchProviderValidation:
    """Launch endpoint validates provider/model before creating a run."""

    def test_unknown_provider_rejected(self, client: TestClient) -> None:
        body = {
            "workflow_id": "assess",
            "target_name": "test",
            "transport": "stdio",
            "command": "echo hi",
            "provider": "fakeprovider",
            "model": "fakeprovider/some-model",
        }
        with patch("q_ai.server.routes.get_workflow") as mock_wf:
            mock_wf.return_value = _mock_workflow_entry("assess")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "Unknown provider" in resp.json()["detail"]

    def test_unconfigured_provider_rejected(self, client: TestClient) -> None:
        body = {
            "workflow_id": "assess",
            "target_name": "test",
            "transport": "stdio",
            "command": "echo hi",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-20250514",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value=None),
            patch("q_ai.server.routes.get_workflow") as mock_wf,
        ):
            mock_wf.return_value = _mock_workflow_entry("assess")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "not configured" in resp.json()["detail"].lower()

    def test_empty_model_rejected(self, client: TestClient) -> None:
        body = {
            "workflow_id": "assess",
            "target_name": "test",
            "transport": "stdio",
            "command": "echo hi",
            "provider": "anthropic",
            "model": "",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_wf,
        ):
            mock_wf.return_value = _mock_workflow_entry("assess")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "model" in resp.json()["detail"].lower()

    def test_valid_cloud_provider_accepted(self, client: TestClient) -> None:
        body = {
            "workflow_id": "assess",
            "target_name": "test",
            "transport": "stdio",
            "command": "echo hi",
            "provider": "openai",
            "model": "openai/gpt-4o",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.server.routes.get_workflow") as mock_wf,
        ):
            mock_wf.return_value = _mock_workflow_entry("assess")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 201
