"""Tests for the provider registry."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from q_ai.core.db import get_connection, set_setting
from q_ai.core.providers import (
    PROVIDERS,
    ProviderType,
    fetch_models,
    get_configured_providers,
    get_provider,
)
from q_ai.core.schema import migrate


class TestProviderRegistry:
    """get_provider() returns config for known providers, None for unknown."""

    def test_known_provider_returns_config(self) -> None:
        config = get_provider("anthropic")
        assert config is not None
        assert config.label == "Anthropic"
        assert config.type == ProviderType.CLOUD
        assert config.supports_custom is True

    def test_all_providers_registered(self) -> None:
        expected = {"anthropic", "openai", "groq", "openrouter", "ollama", "lmstudio", "custom"}
        assert set(PROVIDERS.keys()) == expected

    def test_unknown_provider_returns_none(self) -> None:
        assert get_provider("nonexistent") is None

    def test_local_providers_have_endpoints(self) -> None:
        for name in ("ollama", "lmstudio"):
            config = get_provider(name)
            assert config is not None
            assert config.type == ProviderType.LOCAL
            assert config.models_endpoint is not None
            assert config.default_base_url is not None

    def test_cloud_providers_have_curated_models(self) -> None:
        for name in ("anthropic", "openai", "groq", "openrouter"):
            config = get_provider(name)
            assert config is not None
            assert len(config.curated_models) > 0

    def test_model_ids_include_provider_prefix(self) -> None:
        for name in ("anthropic", "openai", "groq"):
            config = get_provider(name)
            assert config is not None
            for model in config.curated_models:
                assert model.id.startswith(f"{name}/"), f"{model.id} should start with {name}/"

    def test_custom_provider_type(self) -> None:
        config = get_provider("custom")
        assert config is not None
        assert config.type == ProviderType.CUSTOM
        assert config.supports_custom is True

    def test_all_providers_support_custom(self) -> None:
        for name, config in PROVIDERS.items():
            assert config.supports_custom is True, f"{name} should support custom"


class TestFetchModels:
    """fetch_models() returns ModelListResponse for all provider states."""

    @pytest.mark.asyncio
    async def test_cloud_returns_curated_list(self) -> None:
        result = await fetch_models("anthropic", base_url=None)
        assert len(result.models) > 0
        assert result.supports_custom is True
        assert result.error is None
        assert all(m.id.startswith("anthropic/") for m in result.models)

    @pytest.mark.asyncio
    async def test_custom_returns_empty_with_supports_custom(self) -> None:
        result = await fetch_models("custom", base_url="http://localhost:9999")
        assert result.models == []
        assert result.supports_custom is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_ollama_enumerated(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"models": [{"name": "llama3.2"}, {"name": "mistral"}]},
            request=httpx.Request("GET", "http://localhost:11434/api/tags"),
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_models("ollama", base_url="http://localhost:11434")

        assert len(result.models) == 2
        assert result.models[0].id == "ollama/llama3.2"
        assert result.models[0].label == "llama3.2"
        assert result.models[1].id == "ollama/mistral"
        assert result.supports_custom is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_lmstudio_enumerated(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"data": [{"id": "qwen2.5-7b"}, {"id": "phi-3"}]},
            request=httpx.Request("GET", "http://localhost:1234/v1/models"),
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_models("lmstudio", base_url="http://localhost:1234")

        assert len(result.models) == 2
        assert result.models[0].id == "lmstudio/qwen2.5-7b"
        assert result.models[0].label == "qwen2.5-7b"
        assert result.supports_custom is True

    @pytest.mark.asyncio
    async def test_ollama_empty_model_list(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"models": []},
            request=httpx.Request("GET", "http://localhost:11434/api/tags"),
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_models("ollama", base_url="http://localhost:11434")

        assert result.models == []
        assert result.supports_custom is True
        assert result.error is None
        assert result.message is not None
        assert "No models" in result.message

    @pytest.mark.asyncio
    async def test_ollama_unreachable(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_models("ollama", base_url="http://localhost:11434")

        assert result.models == []
        assert result.supports_custom is True
        assert result.error is not None
        assert "connect" in result.error.lower() or "Ollama" in result.error

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_models("ollama", base_url="http://localhost:11434")

        assert result.error is not None
        assert result.models == []

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_error(self) -> None:
        result = await fetch_models("nonexistent", base_url=None)
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_local_uses_default_base_url(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"models": [{"name": "llama3.2"}]},
            request=httpx.Request("GET", "http://localhost:11434/api/tags"),
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fetch_models("ollama", base_url=None)

        assert len(result.models) == 1
        mock_client.get.assert_called_once()
        call_url = mock_client.get.call_args[0][0]
        assert call_url == "http://localhost:11434/api/tags"


@pytest.fixture
def migration_db(tmp_path: Path) -> Path:
    """Create a temp DB with schema for migration tests."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestGetConfiguredProviders:
    """get_configured_providers() returns provider status list."""

    def test_returns_all_providers(self, migration_db: Path) -> None:
        with patch("q_ai.core.providers.get_credential", return_value=None):
            result = get_configured_providers(migration_db)
        assert len(result) == len(PROVIDERS)
        names = {p["name"] for p in result}
        assert names == set(PROVIDERS.keys())

    def test_configured_when_credential_present(self, migration_db: Path) -> None:
        def _mock_cred(p: str) -> str | None:
            return "key" if p == "openai" else None

        with patch("q_ai.core.providers.get_credential", side_effect=_mock_cred):
            result = get_configured_providers(migration_db)

        openai = next(p for p in result if p["name"] == "openai")
        assert openai["configured"] is True

    def test_configured_when_base_url_present(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "ollama.base_url", "http://localhost:11434")

        with patch("q_ai.core.providers.get_credential", return_value=None):
            result = get_configured_providers(migration_db)

        ollama = next(p for p in result if p["name"] == "ollama")
        assert ollama["configured"] is True

    def test_includes_label(self, migration_db: Path) -> None:
        with patch("q_ai.core.providers.get_credential", return_value=None):
            result = get_configured_providers(migration_db)

        anthropic = next(p for p in result if p["name"] == "anthropic")
        assert anthropic["label"] == "Anthropic"
