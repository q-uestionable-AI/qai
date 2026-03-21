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


_FETCH_TIMEOUT_S = 3.0
_log = logging.getLogger(__name__)

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


def _parse_model_list(provider_name: str, data: dict[str, Any]) -> list[ModelInfo]:
    """Parse a JSON response body into a list of ModelInfo objects.

    Handles both Ollama (`models[].name`) and LM Studio (`data[].id`) shapes.
    Each model ID is prefixed with `provider_name/`.

    Args:
        provider_name: Provider key used as the ID prefix.
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

    return [ModelInfo(id=f"{provider_name}/{name}", label=name) for name in raw_names]


async def fetch_models(provider_name: str, base_url: str | None) -> ModelListResponse:
    """Fetch the list of models available from a provider.

    For CLOUD providers the curated registry list is returned with no network
    call.  For CUSTOM providers an empty list is returned with
    ``supports_custom=True``.  For LOCAL providers (Ollama, LM Studio) the
    provider's API endpoint is queried with a 3-second timeout.

    Args:
        provider_name: Provider key (e.g. "anthropic", "ollama").
        base_url: Override base URL for local providers.  When ``None`` the
            registry ``default_base_url`` is used.

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
        return ModelListResponse(
            models=list(config.curated_models),
            supports_custom=config.supports_custom,
        )

    if config.type == ProviderType.CUSTOM:
        return ModelListResponse(models=[], supports_custom=True)

    # LOCAL provider — hit the live endpoint
    return await _fetch_local_models(provider_name, config, base_url)


async def _fetch_local_models(
    provider_name: str,
    config: ProviderConfig,
    base_url: str | None,
) -> ModelListResponse:
    """Query a local provider's API endpoint for available models.

    Args:
        provider_name: Provider key used for ID prefixing and log messages.
        config: Provider configuration from the registry.
        base_url: Caller-supplied base URL override; falls back to
            ``config.default_base_url`` when ``None``.

    Returns:
        ModelListResponse with enumerated models, an empty-but-reachable
        message, or an error message if the endpoint is unreachable.
    """
    resolved_url = base_url or config.default_base_url or ""
    endpoint = f"{resolved_url}{config.models_endpoint}"

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            response = await client.get(endpoint)
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
            error=f"Cannot connect to {config.label} at {resolved_url}.",
        )
    except httpx.HTTPStatusError as exc:
        _log.debug("HTTP error from %s: %s", provider_name, exc)
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"{config.label} returned HTTP {exc.response.status_code}.",
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


def migrate_default_model(db_path: Path | None) -> None:
    """Migrate the legacy ``default_model`` setting to ``default_provider`` + ``default_model_id``.

    Reads ``default_model`` from the settings table and, when present, splits
    it on the first ``/`` to derive a provider key and full model ID.  The
    legacy key is always deleted on exit (whether migration succeeded or was
    skipped).  Safe to call multiple times — idempotent.

    Migration rules:
    - Absent or empty value → no-op, return.
    - ``default_provider`` already present → already migrated; delete
      ``default_model`` and return.
    - Left side is a known provider key AND right side is non-empty → write
      ``default_provider`` (left) and ``default_model_id`` (full original
      value), delete ``default_model``.
    - Otherwise → delete ``default_model``, log a warning.

    Args:
        db_path: Path to the SQLite database file.
    """
    from q_ai.core.db import _delete_setting, get_connection, get_setting, set_setting

    with get_connection(db_path) as conn:
        legacy = get_setting(conn, "default_model")
        if not legacy:
            return

        if get_setting(conn, "default_provider") is not None:
            # Already migrated — clean up the legacy key only.
            _delete_setting(conn, "default_model")
            return

        provider_key, _, model_suffix = legacy.partition("/")

        if provider_key in PROVIDERS and model_suffix:
            set_setting(conn, "default_provider", provider_key)
            set_setting(conn, "default_model_id", legacy)
        else:
            _log.warning(
                "migrate_default_model: cannot parse %r — discarding legacy setting",
                legacy,
            )

        _delete_setting(conn, "default_model")
