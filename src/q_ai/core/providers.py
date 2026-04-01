"""Provider registry — single source of truth for provider definitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from q_ai.core.config import get_credential
from q_ai.core.db import get_connection, get_setting


class ProviderType(Enum):
    """Provider deployment type."""

    CLOUD = "cloud"
    LOCAL = "local"
    CUSTOM = "custom"


class AuthStyle(Enum):
    """How a cloud provider authenticates API requests."""

    BEARER = "bearer"  # Authorization: Bearer <key>
    X_API_KEY = "x-api-key"  # x-api-key: <key>
    QUERY_KEY = "query_key"  # ?key=<key> query parameter


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
        litellm_prefix: Routing prefix for litellm when it differs from the
            registry key (e.g. "gemini" for the "google" provider). None means
            the registry key is used as-is.
    """

    label: str
    type: ProviderType
    supports_custom: bool
    curated_models: list[ModelInfo] = field(default_factory=list)
    default_base_url: str | None = None
    models_endpoint: str | None = None
    auth_style: AuthStyle | None = None
    litellm_prefix: str | None = None


@dataclass
class ModelListResponse:
    """Response from a model list fetch.

    Attributes:
        models: Available models (may be empty).
        supports_custom: Whether free-text model ID input is allowed.
        error: Connection or fetch error message (None on success).
        error_hint: Supplementary hint shown below the error (e.g. auth gateway).
        message: Informational message for empty-but-reachable state.
    """

    models: list[ModelInfo]
    supports_custom: bool
    error: str | None = None
    error_hint: str | None = None
    message: str | None = None


_FETCH_TIMEOUT_S = 3.0
_log = logging.getLogger(__name__)

PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": ProviderConfig(
        label="Anthropic",
        type=ProviderType.CLOUD,
        supports_custom=True,
        default_base_url="https://api.anthropic.com",
        models_endpoint="/v1/models",
        auth_style=AuthStyle.X_API_KEY,
        curated_models=[
            ModelInfo(id="anthropic/claude-sonnet-4-20250514", label="Claude Sonnet 4"),
            ModelInfo(id="anthropic/claude-haiku-4-5-20251001", label="Claude Haiku 4.5"),
        ],
    ),
    "google": ProviderConfig(
        label="Google",
        type=ProviderType.CLOUD,
        supports_custom=True,
        default_base_url="https://generativelanguage.googleapis.com",
        models_endpoint="/v1beta/models",
        auth_style=AuthStyle.QUERY_KEY,
        litellm_prefix="gemini",
        curated_models=[
            ModelInfo(id="gemini/gemini-2.5-pro", label="Gemini 2.5 Pro"),
            ModelInfo(id="gemini/gemini-2.5-flash", label="Gemini 2.5 Flash"),
        ],
    ),
    "openai": ProviderConfig(
        label="OpenAI",
        type=ProviderType.CLOUD,
        supports_custom=True,
        default_base_url="https://api.openai.com",
        models_endpoint="/v1/models",
        auth_style=AuthStyle.BEARER,
        curated_models=[
            ModelInfo(id="openai/gpt-4o", label="GPT-4o"),
            ModelInfo(id="openai/gpt-4o-mini", label="GPT-4o Mini"),
        ],
    ),
    "groq": ProviderConfig(
        label="Groq",
        type=ProviderType.CLOUD,
        supports_custom=True,
    ),
    "openrouter": ProviderConfig(
        label="OpenRouter",
        type=ProviderType.CLOUD,
        supports_custom=True,
    ),
    "xai": ProviderConfig(
        label="xAI",
        type=ProviderType.CLOUD,
        supports_custom=True,
        default_base_url="https://api.x.ai",
        models_endpoint="/v1/models",
        auth_style=AuthStyle.BEARER,
        curated_models=[
            ModelInfo(id="xai/grok-4-1-fast", label="Grok 4.1 Fast"),
            ModelInfo(id="xai/grok-4", label="Grok 4"),
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
        litellm_prefix="lm_studio",
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


def get_litellm_prefix(provider_name: str) -> str:
    """Return the litellm routing prefix for a provider.

    Falls back to the provider key itself when no override is configured.

    Args:
        provider_name: Registry key (e.g. "google", "lmstudio").

    Returns:
        Litellm prefix string (e.g. "gemini", "lm_studio").
    """
    config = PROVIDERS.get(provider_name)
    if config and config.litellm_prefix:
        return config.litellm_prefix
    return provider_name


# Reverse map: litellm prefix → registry key (built once at import time).
_LITELLM_PREFIX_TO_KEY: dict[str, str] = {}
for _key, _cfg in PROVIDERS.items():
    _LITELLM_PREFIX_TO_KEY[_cfg.litellm_prefix or _key] = _key


def registry_key_from_prefix(prefix: str) -> str:
    """Map a litellm prefix back to the provider registry key.

    Args:
        prefix: Litellm routing prefix (e.g. "gemini", "lm_studio").

    Returns:
        Registry key (e.g. "google", "lmstudio"). Falls back to the
        prefix itself if no mapping exists.
    """
    return _LITELLM_PREFIX_TO_KEY.get(prefix, prefix)


def _parse_model_list(provider_name: str, data: dict[str, Any]) -> list[ModelInfo]:
    """Parse a JSON response body into a list of ModelInfo objects.

    Handles both Ollama (`models[].name`) and LM Studio (`data[].id`) shapes.
    Each model ID is prefixed with the provider's litellm routing prefix.

    Args:
        provider_name: Provider key used to look up the litellm prefix.
        data: Parsed JSON response body from the provider's models endpoint.

    Returns:
        List of ModelInfo objects; empty list if the shape is unrecognised.
    """
    if "models" in data:
        raw_names = [m.get("name", "") for m in data["models"] if m.get("name")]
    elif "data" in data:
        raw_names = [m.get("id", "") for m in data["data"] if m.get("id")]
    else:
        _log.warning("Unrecognised model list shape from %s: %s", provider_name, list(data.keys()))
        return []

    prefix = get_litellm_prefix(provider_name)
    return [ModelInfo(id=f"{prefix}/{name}", label=name) for name in raw_names]


async def fetch_models(
    provider_name: str,
    base_url: str | None,
    api_key: str | None = None,
) -> ModelListResponse:
    """Fetch the list of models available from a provider.

    For CLOUD providers with a ``models_endpoint`` and an API key, the
    provider's API is queried live; on failure the curated fallback list is
    returned with a warning.  Aggregator cloud providers (no
    ``models_endpoint``) return an empty list with ``supports_custom=True``
    so the UI renders a free-text model ID input.

    For CUSTOM providers an empty list is returned with
    ``supports_custom=True``.  For LOCAL providers (Ollama, LM Studio) the
    provider's API endpoint is queried with a 3-second timeout.

    Args:
        provider_name: Provider key (e.g. "anthropic", "ollama").
        base_url: Override base URL for local providers.  When ``None`` the
            registry ``default_base_url`` is used.
        api_key: Optional API key forwarded for authentication.

    Returns:
        ModelListResponse describing the available models and any error state.
    """
    config = get_provider(provider_name)
    if config is None:
        return ModelListResponse(
            models=[],
            supports_custom=False,
            error=f"Unknown provider: {provider_name!r}",
        )

    if config.type == ProviderType.CLOUD:
        return await _resolve_cloud_models(provider_name, config, api_key)

    if config.type == ProviderType.CUSTOM:
        return ModelListResponse(models=[], supports_custom=True)

    # LOCAL provider — hit the live endpoint
    return await _fetch_local_models(provider_name, config, base_url, api_key)


async def _resolve_cloud_models(
    provider_name: str,
    config: ProviderConfig,
    api_key: str | None,
) -> ModelListResponse:
    """Resolve the model list for a cloud provider.

    Aggregators (no ``models_endpoint``) get a free-text input.  Direct
    providers are live-fetched when an API key is available, with a
    curated fallback on failure.

    Args:
        provider_name: Provider key.
        config: Provider configuration from the registry.
        api_key: API key for authentication (may be ``None``).

    Returns:
        ModelListResponse for the cloud provider.
    """
    if not config.models_endpoint:
        # Aggregator (Groq, OpenRouter) — free-text model ID only
        return ModelListResponse(models=[], supports_custom=True)
    if not api_key:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            message=f"Enter an API key to load {config.label} models.",
        )
    result = await _fetch_cloud_models(provider_name, config, api_key)
    if result.error and config.curated_models:
        return ModelListResponse(
            models=list(config.curated_models),
            supports_custom=config.supports_custom,
            message=f"Live fetch failed: {result.error}",
        )
    return result


# Per-provider filter rules (case-insensitive substring match).
_GOOGLE_EXCLUDE = ("tts", "lite", "embed", "vision", "nano", "code")
_OPENAI_PREFIXES = ("gpt-4", "gpt-5", "gpt-6", "o1", "o3", "o4", "o5", "o6")
_OPENAI_EXCLUDE = (
    "audio",
    "image",
    "realtime",
    "search",
    "transcrib",
    "embed",
    "dall",
    "tts",
    "whisper",
    "moderation",
)
_OPENAI_DATE_PATTERN = ("-2024-", "-2025-", "-2026-")
_XAI_EXCLUDE = ("vision", "image", "embed", "imagine", "multi-agent")


def _filter_cloud_models(
    provider_name: str,
    data: dict[str, Any],
) -> list[ModelInfo]:
    """Parse and filter a cloud provider's model list response.

    Each provider returns a different shape and needs tight filtering
    to include only current chat/text-generation models.

    Args:
        provider_name: Provider key for ID prefixing and filter selection.
        data: Parsed JSON response from the provider's models endpoint.

    Returns:
        Sorted list of ModelInfo for chat/text-generation models only.
    """
    if provider_name == "google":
        models = _filter_google(data)
    elif provider_name == "anthropic":
        models = _filter_anthropic(data)
    elif provider_name == "openai":
        models = _filter_openai(data)
    elif provider_name == "xai":
        models = _filter_xai(data)
    else:
        models = _filter_generic(provider_name, data)

    models.sort(key=lambda m: m.label, reverse=True)
    return models


def _filter_google(data: dict[str, Any]) -> list[ModelInfo]:
    """Filter Google models: gemini-* prefix + generateContent, exclude non-chat."""
    models: list[ModelInfo] = []
    for m in data.get("models", []):
        name = m.get("name", "")
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        model_id = name.removeprefix("models/")
        if not model_id.startswith("gemini-"):
            continue
        mid_lower = model_id.lower()
        display_lower = m.get("displayName", "").lower()
        if any(pat in mid_lower or pat in display_lower for pat in _GOOGLE_EXCLUDE):
            continue
        display = m.get("displayName", model_id)
        models.append(ModelInfo(id=f"gemini/{model_id}", label=display))
    return models


def _filter_anthropic(data: dict[str, Any]) -> list[ModelInfo]:
    """Filter Anthropic models: only IDs containing 'claude'."""
    models: list[ModelInfo] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if not model_id or "claude" not in model_id.lower():
            continue
        models.append(ModelInfo(id=f"anthropic/{model_id}", label=model_id))
    return models


def _filter_openai(data: dict[str, Any]) -> list[ModelInfo]:
    """Filter OpenAI models: prefix-based inclusion, no dated variants."""
    models: list[ModelInfo] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if not model_id:
            continue
        if not any(model_id.startswith(pfx) for pfx in _OPENAI_PREFIXES):
            continue
        mid_lower = model_id.lower()
        if any(pat in mid_lower for pat in _OPENAI_EXCLUDE):
            continue
        if any(d in model_id for d in _OPENAI_DATE_PATTERN):
            continue
        models.append(ModelInfo(id=f"openai/{model_id}", label=model_id))
    return models


def _filter_xai(data: dict[str, Any]) -> list[ModelInfo]:
    """Filter xAI models: grok-* only, no vision/image/embed."""
    models: list[ModelInfo] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if not model_id or not model_id.startswith("grok-"):
            continue
        mid_lower = model_id.lower()
        if any(pat in mid_lower for pat in _XAI_EXCLUDE):
            continue
        models.append(ModelInfo(id=f"xai/{model_id}", label=model_id))
    return models


def _filter_generic(provider_name: str, data: dict[str, Any]) -> list[ModelInfo]:
    """Fallback filter for providers without specific rules."""
    prefix = get_litellm_prefix(provider_name)
    models: list[ModelInfo] = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if model_id:
            models.append(ModelInfo(id=f"{prefix}/{model_id}", label=model_id))
    return models


def _build_cloud_auth(
    provider_name: str,
    config: ProviderConfig,
    api_key: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build authentication headers and query params for a cloud provider.

    Args:
        provider_name: Provider key (e.g. "anthropic").
        config: Provider configuration from the registry.
        api_key: API key for authentication.

    Returns:
        Tuple of (headers, params) dicts ready for the HTTP request.
    """
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if config.auth_style == AuthStyle.BEARER:
        headers["Authorization"] = f"Bearer {api_key}"
    elif config.auth_style == AuthStyle.X_API_KEY:
        headers["x-api-key"] = api_key
    elif config.auth_style == AuthStyle.QUERY_KEY:
        params["key"] = api_key
    if provider_name == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    return headers, params


async def _fetch_cloud_models(
    provider_name: str,
    config: ProviderConfig,
    api_key: str,
) -> ModelListResponse:
    """Query a cloud provider's API for available models.

    Builds authentication headers or query parameters based on the
    provider's ``auth_style``, then calls the models endpoint and filters
    the response to chat/text-generation models.

    Args:
        provider_name: Provider key for logging and ID prefixing.
        config: Provider configuration from the registry.
        api_key: API key for authentication.

    Returns:
        ModelListResponse with live models or an error message.
    """
    base = config.default_base_url or ""
    endpoint = f"{base}{config.models_endpoint}"

    headers, params = _build_cloud_auth(provider_name, config, api_key)

    error_msg: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            response = await client.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        _log.debug("Timeout fetching models from %s: %s", provider_name, exc)
        error_msg = f"Request to {config.label} timed out after {_FETCH_TIMEOUT_S:.0f}s."
    except httpx.ConnectError as exc:
        _log.debug("Cannot connect to %s: %s", provider_name, exc)
        error_msg = f"Could not reach {config.label} API."
    except httpx.HTTPStatusError as exc:
        _log.debug("HTTP error from %s: %s", provider_name, exc)
        status = exc.response.status_code
        error_msg = (
            "Authentication failed \u2014 check your API key."
            if status in (401, 403)
            else f"{config.label} returned HTTP {status}."
        )

    if error_msg:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=error_msg,
        )

    body = response.json()
    if not isinstance(body, dict):
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"Unexpected response shape from {config.label}.",
        )

    models = _filter_cloud_models(provider_name, body)
    if not models:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            message=f"No chat models found from {config.label}.",
        )
    return ModelListResponse(models=models, supports_custom=config.supports_custom)


async def _fetch_local_models(
    provider_name: str,
    config: ProviderConfig,
    base_url: str | None,
    api_key: str | None = None,
) -> ModelListResponse:
    """Query a local provider's API endpoint for available models.

    Args:
        provider_name: Provider key used for ID prefixing and log messages.
        config: Provider configuration from the registry.
        base_url: Caller-supplied base URL override; falls back to
            ``config.default_base_url`` when ``None``.
        api_key: Optional API key sent as a Bearer token for endpoints
            behind an auth gateway.

    Returns:
        ModelListResponse with enumerated models, an empty-but-reachable
        message, or an error message if the endpoint is unreachable.
    """
    resolved_url = base_url or config.default_base_url or ""
    endpoint = f"{resolved_url}{config.models_endpoint}"

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        _log.debug("Timeout fetching models from %s: %s", provider_name, exc)
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"Request to {config.label} timed out after {_FETCH_TIMEOUT_S:.0f}s.",
        )
    except httpx.ConnectError as exc:
        _log.debug("Cannot connect to %s: %s", provider_name, exc)
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error="Could not reach the server \u2014 check the URL",
        )
    except httpx.HTTPStatusError as exc:
        _log.debug("HTTP error from %s: %s", provider_name, exc)
        status = exc.response.status_code
        if status in (401, 403):
            return ModelListResponse(
                models=[],
                supports_custom=config.supports_custom,
                error="Authentication failed \u2014 check your API key",
                error_hint=("This server requires an API key \u2014 enter one above and retry."),
            )
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"{config.label} returned HTTP {status}.",
        )

    models = _parse_model_list(provider_name, response.json())
    if not models:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            message=f"No models loaded in {config.label}. Pull a model to get started.",
        )
    return ModelListResponse(models=models, supports_custom=config.supports_custom)


def get_configured_providers(db_path: Path | None) -> list[dict[str, Any]]:
    """Check which providers are configured (credentials or base_url present).

    Args:
        db_path: Path to the SQLite database.

    Returns:
        List of dicts with name, label, and configured status for each provider.
    """
    result: list[dict[str, Any]] = []
    with get_connection(db_path) as conn:
        for name, config in PROVIDERS.items():
            try:
                cred = get_credential(name)
            except RuntimeError:
                cred = None
            base_url = get_setting(conn, f"{name}.base_url") or ""
            configured = cred is not None or bool(base_url)
            result.append(
                {
                    "name": name,
                    "label": config.label,
                    "configured": configured,
                }
            )
    return result
