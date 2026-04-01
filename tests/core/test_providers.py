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
    _filter_anthropic,
    _filter_cloud_models,
    _filter_google,
    _filter_openai,
    _filter_xai,
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
        expected = {
            "anthropic",
            "google",
            "openai",
            "groq",
            "openrouter",
            "xai",
            "ollama",
            "lmstudio",
            "custom",
        }
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

    def test_direct_cloud_providers_have_curated_models(self) -> None:
        for name in ("anthropic", "openai", "google", "xai"):
            config = get_provider(name)
            assert config is not None
            assert len(config.curated_models) > 0

    def test_aggregator_providers_have_no_curated_models(self) -> None:
        for name in ("groq", "openrouter"):
            config = get_provider(name)
            assert config is not None
            assert config.curated_models == []
            assert config.models_endpoint is None

    def test_model_ids_include_provider_prefix(self) -> None:
        for name in ("anthropic", "openai", "xai"):
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
    async def test_cloud_without_key_returns_message(self) -> None:
        result = await fetch_models("anthropic", base_url=None)
        assert result.models == []
        assert result.supports_custom is True
        assert result.error is None
        assert result.message is not None
        assert "API key" in result.message

    @pytest.mark.asyncio
    async def test_cloud_with_key_falls_back_to_curated(self) -> None:
        result = await fetch_models("anthropic", base_url=None, api_key="sk-bad")
        # With a bad key the live fetch fails; curated fallback is returned
        assert len(result.models) > 0
        assert result.supports_custom is True
        assert result.error is None
        assert all(m.id.startswith("anthropic/") for m in result.models)

    @pytest.mark.asyncio
    async def test_aggregator_returns_empty_with_supports_custom(self) -> None:
        result = await fetch_models("groq", base_url=None)
        assert result.models == []
        assert result.supports_custom is True
        assert result.error is None

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
        assert "reach" in result.error.lower() or "check the URL" in result.error

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

    @pytest.mark.asyncio
    async def test_anthropic_sends_version_header(self) -> None:
        mock_response = httpx.Response(
            200,
            json={"data": [{"id": "claude-sonnet-4-20250514"}]},
            request=httpx.Request("GET", "https://api.anthropic.com/v1/models"),
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await fetch_models("anthropic", base_url=None, api_key="sk-test")

        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("anthropic-version") == "2023-06-01"
        assert headers.get("x-api-key") == "sk-test"


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


class TestFilterCloudModels:
    """Per-provider cloud model filtering."""

    def test_anthropic_keeps_claude_models(self) -> None:
        data = {
            "data": [
                {"id": "claude-sonnet-4-20250514"},
                {"id": "claude-haiku-4-5-20251001"},
            ]
        }
        result = _filter_anthropic(data)
        assert len(result) == 2
        assert all("claude" in m.id for m in result)

    def test_anthropic_excludes_non_claude(self) -> None:
        data = {
            "data": [
                {"id": "claude-sonnet-4-20250514"},
                {"id": "some-legacy-model"},
                {"id": "deprecated-v1"},
            ]
        }
        result = _filter_anthropic(data)
        assert len(result) == 1
        assert result[0].id == "anthropic/claude-sonnet-4-20250514"

    def test_google_keeps_stable_gemini(self) -> None:
        data = {
            "models": [
                {
                    "name": "models/gemini-2.5-pro",
                    "displayName": "Gemini 2.5 Pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-2.5-flash",
                    "displayName": "Gemini 2.5 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 2

    def test_google_excludes_non_gemini(self) -> None:
        data = {
            "models": [
                {
                    "name": "models/text-embedding-004",
                    "displayName": "Text Embedding 004",
                    "supportedGenerationMethods": ["embedContent"],
                },
                {
                    "name": "models/aqa",
                    "displayName": "AQA",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 0

    def test_google_keeps_all_gemini_versions(self) -> None:
        """No version gate — old and new versions all pass if prefix + method match."""
        data = {
            "models": [
                {
                    "name": "models/gemini-2.0-flash",
                    "displayName": "Gemini 2.0 Flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-1.5-pro",
                    "displayName": "Gemini 1.5 Pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-3.0-pro",
                    "displayName": "Gemini 3.0 Pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 3

    def test_google_keeps_preview_models(self) -> None:
        """Preview models are NOT excluded — many current models are labeled preview."""
        data = {
            "models": [
                {
                    "name": "models/gemini-2.5-flash-preview",
                    "displayName": "Gemini 2.5 Flash Preview",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 1

    def test_google_excludes_nano(self) -> None:
        data = {
            "models": [
                {
                    "name": "models/gemini-2.5-flash-nano",
                    "displayName": "Gemini 2.5 Flash Nano",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 0

    def test_google_excludes_tts_lite_embed_vision_code(self) -> None:
        data = {
            "models": [
                {
                    "name": "models/gemini-2.5-flash-tts",
                    "displayName": "Gemini 2.5 Flash TTS",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-2.5-flash-lite",
                    "displayName": "Gemini 2.5 Flash Lite",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-embedding-exp",
                    "displayName": "Gemini Embedding",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-2.5-pro-vision",
                    "displayName": "Gemini 2.5 Pro Vision",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 0

    def test_google_excludes_by_display_name(self) -> None:
        """Exclusion checks both model ID and displayName."""
        data = {
            "models": [
                {
                    "name": "models/gemini-2.5-special",
                    "displayName": "Gemini 2.5 Nano Banana",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }
        result = _filter_google(data)
        assert len(result) == 0

    def test_openai_keeps_prefix_matched_models(self) -> None:
        data = {
            "data": [
                {"id": "gpt-4.1"},
                {"id": "gpt-4.1-mini"},
                {"id": "gpt-4.1-nano"},
                {"id": "gpt-4o"},
                {"id": "gpt-4o-mini"},
                {"id": "o1"},
                {"id": "o3"},
                {"id": "o3-mini"},
                {"id": "o4-mini"},
            ]
        }
        result = _filter_openai(data)
        assert len(result) == 9
        ids = {m.id for m in result}
        assert "openai/gpt-4.1" in ids
        assert "openai/o3-mini" in ids
        assert "openai/o4-mini" in ids

    def test_openai_keeps_future_gpt(self) -> None:
        data = {"data": [{"id": "gpt-5"}, {"id": "gpt-5-mini"}, {"id": "gpt-6"}]}
        result = _filter_openai(data)
        assert len(result) == 3

    def test_openai_excludes_non_chat_and_non_prefixed(self) -> None:
        data = {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4-turbo"},
                {"id": "babbage-002"},
                {"id": "davinci-002"},
                {"id": "gpt-3.5-turbo"},
                {"id": "gpt-3.5-turbo-0125"},
                {"id": "chatgpt-image-latest"},
                {"id": "gpt-4o-audio-preview"},
                {"id": "gpt-4o-realtime-preview"},
                {"id": "gpt-4o-search-preview"},
                {"id": "gpt-4o-transcription"},
            ]
        }
        result = _filter_openai(data)
        ids = {m.id for m in result}
        assert "openai/gpt-4o" in ids
        assert "openai/gpt-4-turbo" in ids
        # Non-prefixed and non-chat models excluded
        assert "openai/babbage-002" not in ids
        assert "openai/gpt-3.5-turbo" not in ids
        assert "openai/chatgpt-image-latest" not in ids
        assert "openai/gpt-4o-audio-preview" not in ids
        assert "openai/gpt-4o-realtime-preview" not in ids
        assert "openai/gpt-4o-search-preview" not in ids

    def test_openai_excludes_dated_variants(self) -> None:
        data = {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4o-2024-11-20"},
                {"id": "gpt-4o-mini-2024-07-18"},
                {"id": "o1-2025-04-16"},
            ]
        }
        result = _filter_openai(data)
        assert len(result) == 1
        assert result[0].id == "openai/gpt-4o"

    def test_openai_excludes_transcribe(self) -> None:
        data = {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4o-mini-transcribe"},
            ]
        }
        result = _filter_openai(data)
        assert len(result) == 1
        assert result[0].id == "openai/gpt-4o"

    def test_xai_keeps_grok_text_models(self) -> None:
        data = {
            "data": [
                {"id": "grok-4"},
                {"id": "grok-4-1-fast"},
                {"id": "grok-3"},
            ]
        }
        result = _filter_xai(data)
        assert len(result) == 3

    def test_xai_excludes_vision_image_embed_imagine_multiagent(self) -> None:
        data = {
            "data": [
                {"id": "grok-4"},
                {"id": "grok-2-vision-1212"},
                {"id": "grok-image-gen"},
                {"id": "grok-embed-v1"},
                {"id": "grok-imagine-video"},
                {"id": "grok-multi-agent-v1"},
                {"id": "some-other-model"},
            ]
        }
        result = _filter_xai(data)
        assert len(result) == 1
        assert result[0].id == "xai/grok-4"

    def test_filter_cloud_models_sorts_newest_first(self) -> None:
        data = {
            "data": [
                {"id": "claude-haiku-4-5-20251001"},
                {"id": "claude-sonnet-4-20250514"},
                {"id": "claude-opus-4-20250514"},
            ]
        }
        result = _filter_cloud_models("anthropic", data)
        labels = [m.label for m in result]
        assert labels == sorted(labels, reverse=True)

    def test_filter_cloud_models_dispatches_per_provider(self) -> None:
        """Each provider key routes to its specific filter."""
        openai_data = {"data": [{"id": "gpt-4o"}, {"id": "babbage-002"}]}
        result = _filter_cloud_models("openai", openai_data)
        assert len(result) == 1
        assert result[0].id == "openai/gpt-4o"
