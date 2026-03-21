"""Tests for the provider registry."""

from __future__ import annotations

from q_ai.core.providers import PROVIDERS, ProviderType, get_provider


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
