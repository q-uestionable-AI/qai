"""Provider registry — single source of truth for provider definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ProviderType(Enum):
    """Provider deployment type."""

    CLOUD = "cloud"
    LOCAL = "local"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ModelInfo:
    """A model available from a provider.

    Attributes:
        id: Litellm-ready string, e.g. "anthropic/claude-sonnet-4-20250514".
        label: Human-readable display name, e.g. "Claude Sonnet 4".
    """

    id: str
    label: str


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration and capabilities for a provider.

    Attributes:
        label: Human-readable provider name.
        type: Cloud, local, or custom deployment.
        supports_custom: Whether free-text model ID input is allowed.
        curated_models: Static model list for cloud providers.
        default_base_url: Default endpoint URL for local providers.
        models_endpoint: API path for live model enumeration.
    """

    label: str
    type: ProviderType
    supports_custom: bool
    curated_models: list[ModelInfo] = field(default_factory=list)
    default_base_url: str | None = None
    models_endpoint: str | None = None


@dataclass
class ModelListResponse:
    """Response from a model list fetch.

    Attributes:
        models: Available models (may be empty).
        supports_custom: Whether free-text model ID input is allowed.
        error: Connection or fetch error message (None on success).
        message: Informational message for empty-but-reachable state.
    """

    models: list[ModelInfo]
    supports_custom: bool
    error: str | None = None
    message: str | None = None


PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": ProviderConfig(
        label="Anthropic",
        type=ProviderType.CLOUD,
        supports_custom=True,
        curated_models=[
            ModelInfo(id="anthropic/claude-sonnet-4-20250514", label="Claude Sonnet 4"),
            ModelInfo(id="anthropic/claude-haiku-4-5-20251001", label="Claude Haiku 4.5"),
        ],
    ),
    "openai": ProviderConfig(
        label="OpenAI",
        type=ProviderType.CLOUD,
        supports_custom=True,
        curated_models=[
            ModelInfo(id="openai/gpt-4o", label="GPT-4o"),
            ModelInfo(id="openai/gpt-4o-mini", label="GPT-4o Mini"),
        ],
    ),
    "groq": ProviderConfig(
        label="Groq",
        type=ProviderType.CLOUD,
        supports_custom=True,
        curated_models=[
            ModelInfo(id="groq/llama-3.3-70b-versatile", label="Llama 3.3 70B"),
            ModelInfo(id="groq/mixtral-8x7b-32768", label="Mixtral 8x7B"),
        ],
    ),
    "openrouter": ProviderConfig(
        label="OpenRouter",
        type=ProviderType.CLOUD,
        supports_custom=True,
        curated_models=[
            ModelInfo(
                id="openrouter/anthropic/claude-sonnet-4-20250514",
                label="Claude Sonnet 4",
            ),
            ModelInfo(
                id="openrouter/meta-llama/llama-3.3-70b-instruct",
                label="Llama 3.3 70B",
            ),
            ModelInfo(
                id="openrouter/google/gemini-2.5-flash-preview",
                label="Gemini 2.5 Flash",
            ),
        ],
    ),
    "ollama": ProviderConfig(
        label="Ollama",
        type=ProviderType.LOCAL,
        supports_custom=True,
        default_base_url="http://localhost:11434",
        models_endpoint="/api/tags",
    ),
    "lmstudio": ProviderConfig(
        label="LM Studio",
        type=ProviderType.LOCAL,
        supports_custom=True,
        default_base_url="http://localhost:1234",
        models_endpoint="/v1/models",
    ),
    "custom": ProviderConfig(
        label="Custom",
        type=ProviderType.CUSTOM,
        supports_custom=True,
    ),
}


def get_provider(name: str) -> ProviderConfig | None:
    """Look up a provider by key.

    Args:
        name: Provider key (e.g. "anthropic", "ollama").

    Returns:
        ProviderConfig if found, None otherwise.
    """
    return PROVIDERS.get(name)
