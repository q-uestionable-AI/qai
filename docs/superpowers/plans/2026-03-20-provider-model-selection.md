# Provider and Model Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken single-dropdown model selector with a two-step HTMX provider/model selector backed by a provider registry, live model fetching, and launch-time validation.

**Architecture:** New `providers.py` module in `core/` owns the provider registry, model fetching, and settings migration. Routes.py gets a new `/api/providers/{name}/models` endpoint returning HTML partials. Two new Jinja2 partials (`model_selector.html`, `model_area.html`) replace the `model_dropdown()` macro. Launch validation moves from credential-only checking to full provider/model validation.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, HTMX, httpx (async HTTP for model fetching), SQLite, pytest

**Spec:** `docs/superpowers/specs/2026-03-20-provider-model-selection-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/q_ai/core/providers.py` | Create | Provider registry, types, `fetch_models()`, `get_configured_providers()`, `migrate_default_model()` |
| `src/q_ai/server/routes.py` | Modify | New endpoint, updated launcher/settings routes, new launch validation, remove `_build_model_options` and `_check_provider_credential` |
| `src/q_ai/server/templates/launcher.html` | Modify | Remove `model_dropdown()` macro, replace with `{% include %}` |
| `src/q_ai/server/templates/partials/model_selector.html` | Create | Two-step selector: provider dropdown + model area container |
| `src/q_ai/server/templates/partials/model_area.html` | Create | Model area partial (HTMX response fragment) |
| `src/q_ai/server/templates/partials/defaults_section.html` | Modify | Replace text input with selector include, split fields |
| `src/q_ai/server/templates/partials/providers_section.html` | Modify | Replace hardcoded provider list with registry-driven loop |
| `docs/Architecture.md` | Modify | Document provider registry, selector, endpoint, settings change |
| `tests/core/test_providers.py` | Create | Registry, fetch, migration tests |
| `tests/server/test_model_selector.py` | Create | Endpoint and selector integration tests |
| `tests/server/test_templates.py` | Modify | Update assertions for new selector markup |
| `tests/server/test_launcher_phase2.py` | Modify | Update model options and defaults assertions |
| `tests/server/test_settings.py` | Modify | Update defaults save/get tests |
| `tests/server/test_launch.py` | Modify | Add provider/model validation cases, update `_valid_body()` |
| `tests/server/test_run_correctness.py` | Modify | Update launch payloads to include `provider` field |

---

## Task 1: Provider Registry — Types and Registry Dict

**Files:**
- Create: `src/q_ai/core/providers.py`
- Test: `tests/core/test_providers.py`

- [ ] **Step 1: Write failing tests for `get_provider()`**

```python
# tests/core/test_providers.py
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
                assert model.id.startswith(f"{name}/"), (
                    f"{model.id} should start with {name}/"
                )

    def test_custom_provider_type(self) -> None:
        config = get_provider("custom")
        assert config is not None
        assert config.type == ProviderType.CUSTOM
        assert config.supports_custom is True

    def test_all_providers_support_custom(self) -> None:
        for name, config in PROVIDERS.items():
            assert config.supports_custom is True, f"{name} should support custom"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'q_ai.core.providers'`

- [ ] **Step 3: Write the provider registry module**

```python
# src/q_ai/core/providers.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_providers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add src/q_ai/core/providers.py tests/core/test_providers.py
# commit: "feat: add provider registry with types and curated model lists"
```

---

## Task 2: Model Fetching (`fetch_models`)

**Files:**
- Modify: `src/q_ai/core/providers.py`
- Test: `tests/core/test_providers.py`

- [ ] **Step 1: Write failing tests for `fetch_models()`**

Append to `tests/core/test_providers.py`:

```python
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from q_ai.core.providers import fetch_models, ModelListResponse, ProviderType


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_providers.py::TestFetchModels -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_models'`

- [ ] **Step 3: Implement `fetch_models()`**

Add to `src/q_ai/core/providers.py` (after the `get_provider` function):

```python
import logging

import httpx

_FETCH_TIMEOUT_S = 3.0
_log = logging.getLogger(__name__)


async def fetch_models(
    provider_name: str, base_url: str | None
) -> ModelListResponse:
    """Fetch available models for a provider.

    For LOCAL providers, hits their model-list API with a 3s timeout.
    For CLOUD providers, returns the curated list from the registry.
    For CUSTOM providers, returns an empty list with supports_custom=True.

    Args:
        provider_name: Provider key (e.g. "ollama", "anthropic").
        base_url: Override base URL (uses default if None).

    Returns:
        ModelListResponse with models, error, or message.
    """
    config = get_provider(provider_name)
    if config is None:
        return ModelListResponse(
            models=[], supports_custom=False, error=f"Unknown provider: {provider_name}"
        )

    if config.type == ProviderType.CLOUD:
        return ModelListResponse(
            models=list(config.curated_models),
            supports_custom=config.supports_custom,
        )

    if config.type == ProviderType.CUSTOM:
        return ModelListResponse(
            models=[], supports_custom=config.supports_custom
        )

    # LOCAL provider — fetch live from API
    url = base_url or config.default_base_url
    if not url:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"No base URL configured for {config.label}",
        )

    endpoint = f"{url.rstrip('/')}{config.models_endpoint}"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"Could not connect to {config.label} at {url}",
        )
    except httpx.HTTPStatusError as exc:
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            error=f"{config.label} returned HTTP {exc.response.status_code}",
        )

    models = _parse_model_list(provider_name, data)

    if not models:
        empty_messages = {
            "ollama": "No models loaded in Ollama. Pull a model and refresh.",
            "lmstudio": "No models loaded in LM Studio. Load a model and refresh.",
        }
        return ModelListResponse(
            models=[],
            supports_custom=config.supports_custom,
            message=empty_messages.get(
                provider_name, f"No models found in {config.label}."
            ),
        )

    return ModelListResponse(models=models, supports_custom=config.supports_custom)


def _parse_model_list(provider_name: str, data: dict) -> list[ModelInfo]:
    """Parse provider-specific model list response into ModelInfo list.

    Args:
        provider_name: Provider key for ID prefixing.
        data: Raw JSON response from the provider API.

    Returns:
        List of ModelInfo with provider-prefixed IDs.
    """
    models: list[ModelInfo] = []

    if provider_name == "ollama":
        for entry in data.get("models", []):
            name = entry.get("name", "")
            if name:
                models.append(ModelInfo(id=f"ollama/{name}", label=name))
    elif provider_name == "lmstudio":
        for entry in data.get("data", []):
            model_id = entry.get("id", "")
            if model_id:
                models.append(ModelInfo(id=f"lmstudio/{model_id}", label=model_id))
    else:
        _log.warning("No parser for local provider: %s", provider_name)

    return models
```

Add `import logging` and `import httpx` to the top of the file (after existing imports).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_providers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add src/q_ai/core/providers.py tests/core/test_providers.py
# commit: "feat: add fetch_models() with local provider enumeration and cloud curated lists"
```

---

## Task 3: Settings Migration (`migrate_default_model`)

**Files:**
- Modify: `src/q_ai/core/providers.py`
- Test: `tests/core/test_providers.py`

- [ ] **Step 1: Write failing tests for migration**

Append to `tests/core/test_providers.py`:

```python
import sqlite3
from pathlib import Path

from q_ai.core.db import get_connection, get_setting, set_setting
from q_ai.core.providers import migrate_default_model
from q_ai.core.schema import migrate


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


class TestMigrateDefaultModel:
    """migrate_default_model() splits legacy default_model into two fields."""

    def test_happy_path(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "lmstudio/qwen2.5-7b")

        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") == "lmstudio"
            assert get_setting(conn, "default_model_id") == "lmstudio/qwen2.5-7b"
            assert get_setting(conn, "default_model") is None

    def test_missing_slash(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "gpt-4o")

        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") is None
            assert get_setting(conn, "default_model_id") is None
            assert get_setting(conn, "default_model") is None

    def test_unknown_provider_prefix(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "fakeprovider/some-model")

        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") is None
            assert get_setting(conn, "default_model_id") is None
            assert get_setting(conn, "default_model") is None

    def test_blank_model_id(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "openai/")

        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") is None
            assert get_setting(conn, "default_model") is None

    def test_extra_delimiters(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "openrouter/anthropic/claude-sonnet-4")

        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") == "openrouter"
            assert (
                get_setting(conn, "default_model_id")
                == "openrouter/anthropic/claude-sonnet-4"
            )

    def test_already_migrated(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "openai/gpt-4o")
            set_setting(conn, "default_provider", "openai")
            set_setting(conn, "default_model_id", "openai/gpt-4o")

        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") == "openai"
            assert get_setting(conn, "default_model_id") == "openai/gpt-4o"
            assert get_setting(conn, "default_model") is None

    def test_no_default_model(self, migration_db: Path) -> None:
        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") is None
            assert get_setting(conn, "default_model_id") is None

    def test_idempotent(self, migration_db: Path) -> None:
        with get_connection(migration_db) as conn:
            set_setting(conn, "default_model", "ollama/llama3.2")

        migrate_default_model(migration_db)
        migrate_default_model(migration_db)

        with get_connection(migration_db) as conn:
            assert get_setting(conn, "default_provider") == "ollama"
            assert get_setting(conn, "default_model_id") == "ollama/llama3.2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_providers.py::TestMigrateDefaultModel -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_default_model'`

- [ ] **Step 3: Implement `migrate_default_model()`**

Add to `src/q_ai/core/providers.py`:

```python
from pathlib import Path

from q_ai.core.db import get_connection, get_setting, set_setting


def _delete_setting(conn: object, key: str) -> None:
    """Delete a setting from the DB.

    Args:
        conn: SQLite connection.
        key: Setting key to delete.
    """
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))  # type: ignore[union-attr]


def migrate_default_model(db_path: Path) -> None:
    """Migrate legacy default_model to default_provider + default_model_id.

    Idempotent — safe to call multiple times. If the legacy value cannot be
    parsed (unknown provider, missing slash, blank model id), the legacy
    value is deleted and the user must re-select defaults.

    Args:
        db_path: Path to the SQLite database.
    """
    with get_connection(db_path) as conn:
        legacy = get_setting(conn, "default_model")
        if not legacy:
            return

        existing_provider = get_setting(conn, "default_provider")
        if existing_provider is not None:
            # Already migrated — clean up legacy key
            _delete_setting(conn, "default_model")
            return

        if "/" not in legacy:
            _log.warning("Cannot migrate default_model=%r: no slash", legacy)
            _delete_setting(conn, "default_model")
            return

        provider, model_id = legacy.split("/", 1)
        if provider not in PROVIDERS:
            _log.warning(
                "Cannot migrate default_model=%r: unknown provider %r",
                legacy,
                provider,
            )
            _delete_setting(conn, "default_model")
            return

        if not model_id:
            _log.warning("Cannot migrate default_model=%r: blank model id", legacy)
            _delete_setting(conn, "default_model")
            return

        set_setting(conn, "default_provider", provider)
        set_setting(conn, "default_model_id", legacy)
        _delete_setting(conn, "default_model")
```

Note: check if `q_ai.core.db` already has a `delete_setting` function. If so, use it instead of `_delete_setting`. If not, add the helper above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_providers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add src/q_ai/core/providers.py tests/core/test_providers.py
# commit: "feat: add migrate_default_model() with edge case handling"
```

---

## Task 4: `get_configured_providers()`

**Files:**
- Modify: `src/q_ai/core/providers.py`
- Test: `tests/core/test_providers.py`

- [ ] **Step 1: Write failing test**

Append to `tests/core/test_providers.py`:

```python
from q_ai.core.providers import get_configured_providers


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_providers.py::TestGetConfiguredProviders -v`
Expected: FAIL

- [ ] **Step 3: Implement `get_configured_providers()`**

Add to `src/q_ai/core/providers.py`:

```python
from q_ai.core.config import get_credential


def get_configured_providers(db_path: Path) -> list[dict[str, Any]]:
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
            result.append({
                "name": name,
                "label": config.label,
                "configured": configured,
            })
    return result
```

Add `from typing import Any` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_providers.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add src/q_ai/core/providers.py tests/core/test_providers.py
# commit: "feat: add get_configured_providers() to provider registry"
```

---

## Task 5: Model Area HTML Partial

**Files:**
- Create: `src/q_ai/server/templates/partials/model_area.html`

- [ ] **Step 1: Create the model area partial**

This template handles all four response states via conditionals on the data passed from the route.

```html
{# Model area partial — swapped in via HTMX when a provider is selected.
   Context variables: models, supports_custom, error, message,
                      selector_id, provider_name, default_model_id, provider_type #}

{% if error %}
{# State: unreachable or error #}
<div style="border: 1px solid #ef4444; border-radius: 0.5rem; padding: 0.5rem 0.75rem;
            background: rgba(239,68,68,0.08); font-size: 0.8rem;">
    <div style="color: #ef4444; font-weight: 600; margin-bottom: 0.15rem;">{{ error }}</div>
    <div style="opacity: 0.7;">Check that the provider is running.
        <a href="/settings#providers" style="color: #7c3aed; text-decoration: underline;">Settings</a>
    </div>
</div>
{% if supports_custom %}
<div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.35rem;">
    {% if provider_type == "local" %}
    <button type="button" class="btn btn-xs btn-ghost"
            onclick="htmx.ajax('GET', '/api/providers/{{ provider_name }}/models?default={{ default_model_id or '' }}&selector_id={{ selector_id }}', {target: '#{{ selector_id }}-model-area'})">
        &#8635; Retry
    </button>
    {% else %}
    <span></span>
    {% endif %}
    <label style="font-size: 0.7rem; opacity: 0.5; font-style: italic; cursor: pointer;"
           onclick="document.getElementById('{{ selector_id }}-custom-wrap').classList.toggle('hidden')">
        or type a model ID manually
    </label>
</div>
<div id="{{ selector_id }}-custom-wrap" class="hidden mt-1">
    <input type="text" name="model"
           placeholder="provider/model-id"
           class="input input-bordered input-sm w-full" />
</div>
{% endif %}

{% elif message %}
{# State: reachable but empty model list #}
<div style="border: 1px solid #f59e0b; border-radius: 0.5rem; padding: 0.5rem 0.75rem;
            background: rgba(245,158,11,0.08); font-size: 0.8rem;">
    <div style="color: #f59e0b; font-weight: 600; margin-bottom: 0.15rem;">No models loaded</div>
    <div style="opacity: 0.7;">{{ message }}</div>
</div>
{% if supports_custom %}
<div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.35rem;">
    <button type="button" class="btn btn-xs btn-ghost"
            onclick="htmx.ajax('GET', '/api/providers/{{ provider_name }}/models?default={{ default_model_id or '' }}&selector_id={{ selector_id }}', {target: '#{{ selector_id }}-model-area'})">
        &#8635; Refresh models
    </button>
    <label style="font-size: 0.7rem; opacity: 0.5; font-style: italic; cursor: pointer;"
           onclick="document.getElementById('{{ selector_id }}-custom-wrap').classList.toggle('hidden')">
        or type a model ID manually
    </label>
</div>
<div id="{{ selector_id }}-custom-wrap" class="hidden mt-1">
    <input type="text" name="model"
           placeholder="provider/model-id"
           class="input input-bordered input-sm w-full" />
</div>
{% endif %}

{% else %}
{# State: models loaded (enumerated or curated) #}
<select name="model" required
        class="select select-bordered select-sm w-full"
        onchange="toggleCustomModel_{{ selector_id }}(this)">
    <option value="" disabled {% if not default_model_id %}selected{% endif %}>Select a model...</option>
    {% for m in models %}
    <option value="{{ m.id }}"
            {% if m.id == default_model_id %}selected{% endif %}>{{ m.label }}</option>
    {% endfor %}
    {% if supports_custom %}
    <option value="__custom__">Custom model id...</option>
    {% endif %}
</select>

{% if provider_type == "local" %}
<div style="display: flex; justify-content: flex-end; margin-top: 0.25rem;">
    <button type="button" class="btn btn-xs btn-ghost"
            onclick="htmx.ajax('GET', '/api/providers/{{ provider_name }}/models?default={{ default_model_id or '' }}&selector_id={{ selector_id }}', {target: '#{{ selector_id }}-model-area'})">
        &#8635; Refresh models
    </button>
</div>
{% endif %}

{% if supports_custom %}
<div id="{{ selector_id }}-custom-wrap" class="hidden mt-1">
    <input type="text" id="{{ selector_id }}-custom-input"
           placeholder="provider/model-id"
           class="input input-bordered input-sm w-full" />
</div>
<script>
function toggleCustomModel_{{ selector_id }}(sel) {
    var wrap = document.getElementById('{{ selector_id }}-custom-wrap');
    var inp = document.getElementById('{{ selector_id }}-custom-input');
    if (sel.value === '__custom__') {
        wrap.classList.remove('hidden');
        inp.name = 'model';
        inp.required = true;
        sel.name = '';
        sel.required = false;
    } else {
        wrap.classList.add('hidden');
        inp.name = '';
        inp.required = false;
        sel.name = 'model';
        sel.required = true;
    }
}
</script>
{% endif %}
{% endif %}
```

- [ ] **Step 2: Commit**

```
git add src/q_ai/server/templates/partials/model_area.html
# commit: "feat: add model_area.html partial for HTMX model selector states"
```

---

## Task 6: Model Selector Partial and Launcher Template Update

**Files:**
- Create: `src/q_ai/server/templates/partials/model_selector.html`
- Modify: `src/q_ai/server/templates/launcher.html` (lines 64-76: remove macro; lines 160, 274, 377: replace calls)

- [ ] **Step 1: Create model_selector.html**

```html
{# Two-step provider/model selector.
   Required context: selector_id (unique per form), providers (configured list),
                     default_provider, default_model_id #}

<div class="form-control">
    <label class="label"><span class="label-text">Provider</span></label>
    <select name="provider" required
            id="{{ selector_id }}-provider"
            class="select select-bordered select-sm w-full"
            onchange="htmx.ajax('GET', '/api/providers/' + this.value + '/models?default={{ default_model_id or '' }}&selector_id={{ selector_id }}', {target: '#' + '{{ selector_id }}-model-area'})">
        <option value="" disabled {% if not default_provider %}selected{% endif %}>
            Select a provider...
        </option>
        {% for p in providers %}
        <option value="{{ p.name }}"
                {% if p.name == default_provider %}selected{% endif %}>
            {{ p.label }}
        </option>
        {% endfor %}
    </select>
</div>

<div class="form-control">
    <label class="label"><span class="label-text">Model</span></label>
    <div id="{{ selector_id }}-model-area">
        <select disabled class="select select-bordered select-sm w-full opacity-40">
            <option>Select a provider first</option>
        </select>
    </div>
</div>
```

- [ ] **Step 2: Update launcher.html — remove macro, replace with includes**

In `launcher.html`:

Remove lines 64-76 (the `model_dropdown()` macro).

Replace line 160 (`{{ model_dropdown() }}`) with:
```jinja2
{% set selector_id = "assess" %}
{% include "partials/model_selector.html" %}
```

Replace line 274 (`{{ model_dropdown() }}`) with:
```jinja2
{% set selector_id = "trace_path" %}
{% include "partials/model_selector.html" %}
```

Replace line 377 (`{{ model_dropdown() }}`) with:
```jinja2
{% set selector_id = "campaign" %}
{% include "partials/model_selector.html" %}
```

- [ ] **Step 3: Add DOMContentLoaded auto-fetch script**

Add before the closing `{% endblock %}` in `launcher.html`:

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[id$="-provider"]').forEach(function(sel) {
        if (sel.value && sel.value !== '') {
            var selectorId = sel.id.replace('-provider', '');
            var defaultModel = '{{ default_model_id or "" }}';
            htmx.ajax('GET',
                '/api/providers/' + sel.value + '/models?default=' + encodeURIComponent(defaultModel) + '&selector_id=' + selectorId,
                {target: '#' + selectorId + '-model-area'});
        }
    });
});
</script>
```

- [ ] **Step 4: Verify the launcher page loads without errors**

Run: `uv run qai --help` (smoke test CLI)
Run: `uv run pytest tests/server/test_templates.py::TestWorkflowAccordion -v`
Expected: PASS (accordion structure tests should still pass)

- [ ] **Step 5: Commit**

```
git add src/q_ai/server/templates/partials/model_selector.html src/q_ai/server/templates/launcher.html
# commit: "feat: replace model_dropdown() macro with HTMX two-step selector"
```

---

## Task 7: API Endpoint and Route Updates

**Files:**
- Modify: `src/q_ai/server/routes.py` (lines 64-115: launcher route; lines 118-145: remove `_build_model_options`; lines 1335-1367: update `_get_providers_status`; lines 1375-1391: settings route; lines 1542-1570: defaults routes; lines 1971-2021: replace `_check_provider_credential`; lines 2101-2176: update `launch_workflow`)
- Test: `tests/server/test_model_selector.py`

- [ ] **Step 1: Write failing tests for the new endpoint**

```python
# tests/server/test_model_selector.py
"""Tests for the provider models endpoint and model selector."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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

    def test_local_provider_enumerated(self, client: TestClient) -> None:
        mock_response = ModelListResponse(
            models=[
                ModelInfo(id="ollama/llama3.2", label="llama3.2"),
                ModelInfo(id="ollama/mistral", label="mistral"),
            ],
            supports_custom=True,
        )
        with (
            patch("q_ai.server.routes.fetch_models", new_callable=AsyncMock, return_value=mock_response),
            patch("q_ai.server.routes.get_credential", return_value=None),
        ):
            resp = client.get(
                "/api/providers/ollama/models?selector_id=test",
            )
        assert resp.status_code == 200
        assert "llama3.2" in resp.text
        assert "mistral" in resp.text

    def test_empty_model_list_shows_message(self, client: TestClient) -> None:
        mock_response = ModelListResponse(
            models=[],
            supports_custom=True,
            message="No models loaded in Ollama. Pull a model and refresh.",
        )
        with (
            patch("q_ai.server.routes.fetch_models", new_callable=AsyncMock, return_value=mock_response),
            patch("q_ai.server.routes.get_credential", return_value=None),
        ):
            resp = client.get(
                "/api/providers/ollama/models?selector_id=test",
            )
        assert resp.status_code == 200
        assert "No models loaded" in resp.text

    def test_unreachable_shows_error(self, client: TestClient) -> None:
        mock_response = ModelListResponse(
            models=[],
            supports_custom=True,
            error="Could not connect to Ollama at http://localhost:11434",
        )
        with (
            patch("q_ai.server.routes.fetch_models", new_callable=AsyncMock, return_value=mock_response),
            patch("q_ai.server.routes.get_credential", return_value=None),
        ):
            resp = client.get(
                "/api/providers/ollama/models?selector_id=test",
            )
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/server/test_model_selector.py -v`
Expected: FAIL — 404 (endpoint doesn't exist yet)

- [ ] **Step 3: Implement route changes in `routes.py`**

**3a. Add imports** (top of `routes.py`, after existing imports):

```python
from q_ai.core.providers import (
    PROVIDERS,
    ProviderType,
    fetch_models,
    get_configured_providers,
    get_provider,
    migrate_default_model,
)
```

**3b. Add the new endpoint** (after the settings routes section):

```python
@router.get("/api/providers/{name}/models")
async def api_provider_models(request: Request, name: str) -> Response:
    """Fetch models for a provider and return an HTML partial.

    Query params:
        default: default_model_id for pre-selection.
        selector_id: DOM ID prefix for the model area.
    """
    templates = _get_templates(request)
    config = get_provider(name)
    if config is None:
        return HTMLResponse(
            content="<div class='text-error text-sm'>Unknown provider</div>",
            status_code=404,
        )

    db_path = _get_db_path(request)
    # Check provider is configured
    with get_connection(db_path) as conn:
        try:
            cred = get_credential(name)
        except RuntimeError:
            cred = None
        base_url = get_setting(conn, f"{name}.base_url") or ""

    configured = cred is not None or bool(base_url)
    if not configured and config.type != ProviderType.CUSTOM:
        return HTMLResponse(
            content=(
                "<div class='text-error text-sm'>Provider not configured. "
                "<a href='/settings#providers' class='link'>Settings</a></div>"
            ),
            status_code=400,
        )

    result = await fetch_models(name, base_url or None)

    selector_id = request.query_params.get("selector_id", "default")
    default_model_id = request.query_params.get("default", "")

    return templates.TemplateResponse(
        request,
        "partials/model_area.html",
        {
            "models": result.models,
            "supports_custom": result.supports_custom,
            "error": result.error,
            "message": result.message,
            "selector_id": selector_id,
            "provider_name": name,
            "default_model_id": default_model_id,
            "provider_type": config.type.value,
        },
    )
```

**3c. Update launcher route** (`routes.py` lines 64-115):

Replace the `model_options` and `default_model` logic. The launcher route should now:
- Call `migrate_default_model(db_path)` before reading settings
- Read `default_provider` and `default_model_id` instead of `default_model`
- Pass configured providers with labels from `get_configured_providers()`
- Remove the `_build_model_options()` call and `model_options` context variable

```python
# In the launcher() function, replace lines 87-112:
    all_providers = get_configured_providers(db_path)
    providers = [p for p in all_providers if p["configured"]]

    migrate_default_model(db_path)

    with get_connection(db_path) as conn:
        default_provider = get_setting(conn, "default_provider") or ""
        default_model_id = get_setting(conn, "default_model_id") or ""
        default_transport = get_setting(conn, "audit.default_transport") or "stdio"
        defaults = {
            "ipi_callback_url": get_setting(conn, "ipi.default_callback_url") or "",
            "audit_default_transport": default_transport,
        }

    return templates.TemplateResponse(
        request,
        "launcher.html",
        {
            "active": "launcher",
            "hero_workflow": hero_workflow,
            "workflows": workflows,
            "providers": providers,
            "default_provider": default_provider,
            "default_model_id": default_model_id,
            "rxp_available": rxp_is_available(),
            "defaults": defaults,
        },
    )
```

**3d. Delete `_build_model_options()`** (lines 118-145) — no longer used.

**3e. Update settings route** (lines 1375-1391):

```python
    migrate_default_model(db_path)
    with get_connection(db_path) as conn:
        defaults = {
            "default_provider": get_setting(conn, "default_provider") or "",
            "default_model_id": get_setting(conn, "default_model_id") or "",
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
        }
```

**3f. Update defaults GET/POST routes** (lines 1542-1570):

GET route returns `default_provider` + `default_model_id` instead of `default_model`.

POST route accepts `default_provider` + `default_model_id`:

```python
    allowed_keys = (
        "default_provider",
        "default_model_id",
        "audit.default_transport",
        "ipi.default_callback_url",
    )
```

**3g. Update `_get_providers_status()`** (lines 1335-1367):

Replace the hardcoded `known_providers` list with iteration over `PROVIDERS`. Keep the full return shape for the JSON endpoint:

```python
def _get_providers_status(request: Request) -> list[dict[str, Any]]:
    db_path = _get_db_path(request)
    result: list[dict[str, Any]] = []
    with get_connection(db_path) as conn:
        for name, config in PROVIDERS.items():
            keyring_unavailable = False
            try:
                cred = get_credential(name)
            except RuntimeError:
                cred = None
                keyring_unavailable = True
            base_url = get_setting(conn, f"{name}.base_url") or ""
            configured = cred is not None or bool(base_url)
            result.append({
                "name": name,
                "configured": configured,
                "has_key": cred is not None,
                "base_url": base_url,
                "keyring_unavailable": keyring_unavailable,
            })
    return result
```

- [ ] **Step 4: Run endpoint tests**

Run: `uv run pytest tests/server/test_model_selector.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add src/q_ai/server/routes.py tests/server/test_model_selector.py
# commit: "feat: add /api/providers/{name}/models endpoint, update launcher and settings routes"
```

---

## Task 8: Launch Validation

**Files:**
- Modify: `src/q_ai/server/routes.py` (lines 1971-2021: replace `_check_provider_credential`; lines 2101-2176: update `launch_workflow`)
- Test: `tests/server/test_model_selector.py` (add validation tests)
- Modify: `tests/server/test_launch.py` (update existing tests)

- [ ] **Step 1: Write failing tests for launch validation**

Append to `tests/server/test_model_selector.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock


def _mock_workflow_entry(wf_id: str, *, requires_provider: bool = True) -> MagicMock:
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

    def test_unreachable_local_provider_rejected(self, client: TestClient) -> None:
        unreachable = ModelListResponse(
            models=[], supports_custom=True,
            error="Could not connect to Ollama",
        )
        body = {
            "workflow_id": "assess",
            "target_name": "test",
            "transport": "stdio",
            "command": "echo hi",
            "provider": "ollama",
            "model": "ollama/llama3.2",
        }
        with (
            patch("q_ai.server.routes.get_credential", return_value=None),
            patch("q_ai.server.routes.fetch_models", new_callable=AsyncMock, return_value=unreachable),
            patch("q_ai.server.routes.get_workflow") as mock_wf,
            patch("q_ai.server.routes.get_setting", return_value="http://localhost:11434"),
        ):
            mock_wf.return_value = _mock_workflow_entry("assess")
            resp = client.post("/api/workflows/launch", json=body)
        assert resp.status_code == 422
        assert "connect" in resp.json()["detail"].lower() or "unreachable" in resp.json()["detail"].lower()

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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/server/test_model_selector.py::TestLaunchProviderValidation -v`
Expected: FAIL

- [ ] **Step 3: Implement new validation in `launch_workflow`**

Replace `_check_provider_credential` with a new `_validate_provider_model` function:

```python
async def _validate_provider_model(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | None:
    """Validate provider/model pair before launch.

    Returns a JSONResponse error if validation fails, None on success.
    """
    provider_name = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()

    if not provider_name:
        # Backward compat: try to extract from model string
        if model and "/" in model:
            provider_name = model.split("/", 1)[0]
        if not provider_name:
            return JSONResponse(
                status_code=422,
                content={"detail": "provider is required"},
            )

    config = get_provider(provider_name)
    if config is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown provider: {provider_name}"},
        )

    # Check configured
    with get_connection(db_path) as conn:
        try:
            cred = get_credential(provider_name)
        except RuntimeError:
            cred = None
        base_url = get_setting(conn, f"{provider_name}.base_url") or ""

    configured = cred is not None or bool(base_url)
    if not configured and config.type != ProviderType.CUSTOM:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Provider '{provider_name}' is not configured"},
        )

    if not model:
        return JSONResponse(
            status_code=422,
            content={"detail": "No model selected"},
        )

    # For local providers, check reachability via fetch_models
    if config.type == ProviderType.LOCAL:
        result = await fetch_models(provider_name, base_url or None)
        if result.error:
            return JSONResponse(
                status_code=422,
                content={"detail": result.error},
            )

    return None
```

In `launch_workflow`, replace the call to `_check_provider_credential(body, db_path)` with `await _validate_provider_model(body, db_path)`.

Delete `_check_provider_credential` (lines 1971-2021).

- [ ] **Step 4: Update `_valid_body()` in `test_launch.py`**

Update the helper to include `provider`:

```python
def _valid_body() -> dict:
    return {
        "target_name": "test-server",
        "transport": "stdio",
        "command": "echo hi",
        "provider": "openai",
        "model": "openai/gpt-4",
        "rounds": 1,
    }
```

Update all tests in `test_launch.py` that pass `model` to also pass `provider`. Update assertion strings where needed (e.g., credential error messages may change).

- [ ] **Step 5: Run all launch tests**

Run: `uv run pytest tests/server/test_model_selector.py tests/server/test_launch.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```
git add src/q_ai/server/routes.py tests/server/test_model_selector.py tests/server/test_launch.py
# commit: "feat: replace credential check with full provider/model validation at launch"
```

---

## Task 9: Settings Page — Defaults Section Update

**Files:**
- Modify: `src/q_ai/server/templates/partials/defaults_section.html`
- Modify: `tests/server/test_settings.py`

- [ ] **Step 1: Update defaults_section.html**

Replace the `default_model` text input with the model selector include. The defaults form now uses the same selector component. Update `saveDefaults()` to submit `default_provider` + `default_model_id`:

```html
<div class="panel p-6">
    <h2 class="text-lg font-semibold mb-4" style="color: #f1f5f9;">Defaults</h2>
    <form id="defaults-form" class="grid grid-cols-1 md:grid-cols-2 gap-4"
          onsubmit="return saveDefaults(event)">

        <div class="md:col-span-2">
            {% set selector_id = "defaults" %}
            {% include "partials/model_selector.html" %}
        </div>

        <div class="form-control">
            <label class="label">
                <span class="label-text">Default Audit Transport</span>
            </label>
            <select name="audit.default_transport"
                    class="select select-bordered select-sm">
                <option value="stdio"
                    {% if defaults['audit.default_transport'] == 'stdio' %}selected{% endif %}>
                    stdio
                </option>
                <option value="sse"
                    {% if defaults['audit.default_transport'] == 'sse' %}selected{% endif %}>
                    sse
                </option>
                <option value="streamable-http"
                    {% if defaults['audit.default_transport'] == 'streamable-http' %}selected{% endif %}>
                    streamable-http
                </option>
            </select>
        </div>

        <div class="form-control">
            <label class="label">
                <span class="label-text">IPI Callback URL</span>
            </label>
            <input type="text" name="ipi.default_callback_url"
                   class="input input-bordered input-sm"
                   placeholder="https://example.com/callback"
                   value="{{ defaults['ipi.default_callback_url'] }}">
        </div>

        <div class="md:col-span-2 flex items-end gap-2">
            <button type="submit" class="btn btn-sm btn-outline btn-primary">Save</button>
            <span id="defaults-msg" class="text-xs"></span>
        </div>
    </form>
</div>

<script>
function saveDefaults(e) {
    e.preventDefault();
    var form = document.getElementById("defaults-form");
    var data = {};
    new FormData(form).forEach(function(v, k) { data[k] = v; });
    // Map provider/model fields to settings keys
    data["default_provider"] = data["provider"] || "";
    data["default_model_id"] = data["model"] || "";
    delete data["provider"];
    delete data["model"];

    var msg = document.getElementById("defaults-msg");
    fetch("/api/settings/defaults", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
    }).then(function(r) { return r.json(); }).then(function(d) {
        if (d.status === "saved") {
            msg.className = "text-xs text-success";
            msg.textContent = "Saved";
        } else {
            msg.className = "text-xs text-error";
            msg.textContent = d.detail || "Error";
        }
    }).catch(function() {
        msg.className = "text-xs text-error";
        msg.textContent = "Request failed";
    });
    return false;
}
</script>
```

- [ ] **Step 2: Update `TestSaveDefaults` in `test_settings.py`**

```python
class TestSaveDefaults:
    """Tests for saving and retrieving default settings."""

    def test_save_defaults(self, client: TestClient) -> None:
        """POST defaults -> GET defaults returns saved values."""
        resp = client.post(
            "/api/settings/defaults",
            json={
                "default_provider": "openai",
                "default_model_id": "openai/gpt-4o",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/settings/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_provider"] == "openai"
        assert data["default_model_id"] == "openai/gpt-4o"
```

- [ ] **Step 3: Run settings tests**

Run: `uv run pytest tests/server/test_settings.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```
git add src/q_ai/server/templates/partials/defaults_section.html tests/server/test_settings.py
# commit: "feat: update Settings defaults to use provider/model selector"
```

---

## Task 10: Update Remaining Tests

**Files:**
- Modify: `tests/server/test_templates.py`
- Modify: `tests/server/test_launcher_phase2.py`
- Modify: `tests/server/test_run_correctness.py`

- [ ] **Step 1: Update `test_templates.py`**

`TestWorkflowAccordion.test_inline_forms_present` — the form still renders, just with a different selector. No changes expected unless the test asserts on `model_options` or `select name="model"`. Check and update if needed.

- [ ] **Step 2: Update `test_launcher_phase2.py`**

**`TestLauncherDefaults.test_defaults_in_context`** (line 49-75):
- Change the DB seed from `default_model` to `default_provider` + `default_model_id`
- Update assertion: instead of `assert "lmstudio/qwen2.5-7b" in resp.text`, check for the provider being pre-selected: `assert 'value="lmstudio" selected' in resp.text` or similar.

**`TestModelOptions`** (lines 78-112):
- `test_default_model_shown`: Rewrite — seed `default_provider` + `default_model_id`, check provider dropdown has the provider selected.
- `test_fallback_to_provider_default`: Remove — `provider/default` no longer exists.
- `test_unconfigured_providers_excluded`: Update — check provider dropdown doesn't contain unconfigured providers.

- [ ] **Step 3: Update `test_run_correctness.py`**

This file doesn't directly test launch payloads through the HTTP endpoint — it tests `WorkflowRunner` and DB operations directly. Scan for any `model` field usage and add `provider` if needed. Most tests here should be unaffected.

- [ ] **Step 4: Run all updated tests together**

Run: `uv run pytest tests/server/test_templates.py tests/server/test_launcher_phase2.py tests/server/test_run_correctness.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add tests/server/test_templates.py tests/server/test_launcher_phase2.py tests/server/test_run_correctness.py
# commit: "test: update existing tests for provider/model selector changes"
```

---

## Task 11: Providers Section Template Update

**Files:**
- Modify: `src/q_ai/server/templates/partials/providers_section.html` (lines 62-72: hardcoded options; lines 97-98: JS constants)

- [ ] **Step 1: Replace hardcoded provider dropdown with registry-driven loop**

Replace lines 62-72 with:

```html
<select id="provider-select" class="select select-bordered select-sm"
        onchange="toggleProviderFields()">
    <option value="" disabled selected>Select provider</option>
    {% for p in providers %}
    <option value="{{ p.name }}">{{ p.name }}</option>
    {% endfor %}
</select>
```

Note: The `providers` context variable already contains all known providers from `_get_providers_status()`, which now iterates `PROVIDERS`.

- [ ] **Step 2: Update JS constants to use provider type from registry**

Replace lines 97-98 with logic that reads provider type from the data. Since the route already passes provider data, embed the cloud set from the template context or keep it simple:

```javascript
const CLOUD = new Set({{ cloud_providers | tojson }});
const LOCAL_DEFAULTS = {{ local_defaults | tojson }};
```

Add to the settings route context: `cloud_providers` and `local_defaults` computed from the `PROVIDERS` registry. Alternatively, keep the JS constants hardcoded but ensure they match the registry — the simpler approach since this template already works.

**Decision:** Keep the JS constants derived from the route context to avoid drift. Add to the settings route:

```python
from q_ai.core.providers import PROVIDERS, ProviderType

cloud_providers = [
    name for name, cfg in PROVIDERS.items() if cfg.type == ProviderType.CLOUD
]
local_defaults = {
    name: cfg.default_base_url
    for name, cfg in PROVIDERS.items()
    if cfg.default_base_url
}
```

Pass `cloud_providers` and `local_defaults` in the template context.

- [ ] **Step 3: Verify settings page renders**

Run: `uv run pytest tests/server/test_settings.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```
git add src/q_ai/server/templates/partials/providers_section.html src/q_ai/server/routes.py
# commit: "refactor: replace hardcoded provider list in providers_section with registry-driven loop"
```

---

## Task 12: Architecture.md Update

**Files:**
- Modify: `docs/Architecture.md`

- [ ] **Step 1: Add Provider Registry section**

After the Core Layer section (after line 112), add:

```markdown
### Provider Registry (`core/providers.py`)

Single source of truth for provider definitions. `PROVIDERS` dict maps provider keys to `ProviderConfig` dataclasses with type (CLOUD/LOCAL/CUSTOM), curated model lists, endpoint URLs, and capability flags. `fetch_models()` enumerates available models from local providers (Ollama, LM Studio) via their APIs with a 3s timeout, or returns curated lists for cloud providers. `get_configured_providers()` checks credential and base_url presence across all registered providers.
```

- [ ] **Step 2: Update LLM Abstraction mention**

In the Core Layer section (line 108), update the `llm.py` bullet:

```markdown
- **`llm.py`** — `ProviderClient` protocol, `NormalizedResponse`, `ToolSpec`, `ToolCall`. `provider/model` string convention. The litellm runtime string is composed at launch time from separate `provider` and `model_id` fields stored in settings.
```

- [ ] **Step 3: Add Provider/Model Selector section**

In the Web Server section (after line 155), add:

```markdown
- `GET /api/providers/{name}/models` — HTMX endpoint returning model area HTML partial. Four states: enumerated (local), curated (cloud), empty (warning), unreachable (error).

**Provider/Model Selector:** Two-step HTMX component (`model_selector.html` + `model_area.html`). Provider dropdown triggers live model fetch via `htmx.ajax()`. Shared across launcher forms and Settings defaults via `{% include %}` with `selector_id` scoping.
```

- [ ] **Step 4: Update Settings mention**

In the Core Layer config bullet, note the settings change:

```markdown
- **`config.py`** — OS keyring for API keys. Non-secret settings in `~/.qai/config.yaml` and DB `settings` table. Provider defaults stored as `default_provider` + `default_model_id` (migrated from legacy `default_model` on first read).
```

- [ ] **Step 5: Commit**

```
git add docs/Architecture.md
# commit: "docs: update Architecture.md with provider registry and model selector"
```

---

## Task 13: Final Verification

- [ ] **Step 1: Run lint and type checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/q_ai/ && uv run pre-commit run --all-files`

Fix any issues.

- [ ] **Step 2: Run all scoped tests**

Run: `uv run pytest tests/core/test_providers.py tests/server/test_model_selector.py tests/server/test_templates.py tests/server/test_launcher_phase2.py tests/server/test_settings.py tests/server/test_launch.py tests/server/test_run_correctness.py -v`

All must PASS.

- [ ] **Step 3: Smoke test the CLI**

Run: `uv run qai --help`
Expected: Help output renders without errors.

- [ ] **Step 4: Final commit if any lint fixes were needed**

```
# commit: "style: fix lint and type check issues"
```
