# Provider and Model Selection — Design Spec

**Date:** 2026-03-20
**Brief:** `findings-output-0c-provider-model-selection.md`
**Branch:** `fix/provider-model-selection`

---

## Problem

The launcher's model dropdown shows `provider/default` ghost entries. Selecting one
launches a workflow that runs audit and proxy, then fails silently at inject with no
useful error. Users see partial results and no explanation.

The root cause: there is no model discovery, no provider capability definition, and no
launch-time validation. The single `default_model` setting stores a raw litellm string
that may not resolve to a real model.

## Solution Overview

Replace the single-dropdown `model_dropdown()` macro with a two-step HTMX
provider/model selector. Add a provider registry module, a model-fetching endpoint,
launch-time backend validation, and split the `default_model` setting into
`default_provider` + `default_model_id`.

---

## 1. Provider Registry (`src/q_ai/core/providers.py`)

New module — single source of truth for provider definitions. Replaces hardcoded
provider lists in `routes.py` and `providers_section.html`.

### Types

```python
class ProviderType(Enum):
    CLOUD = "cloud"
    LOCAL = "local"
    CUSTOM = "custom"

@dataclass(frozen=True)
class ModelInfo:
    id: str      # litellm-ready string, e.g. "anthropic/claude-sonnet-4-20250514"
    label: str   # display name, e.g. "Claude Sonnet 4"

@dataclass(frozen=True)
class ProviderConfig:
    label: str
    type: ProviderType
    supports_custom: bool
    curated_models: list[ModelInfo] = field(default_factory=list)
    default_base_url: str | None = None
    models_endpoint: str | None = None

@dataclass
class ModelListResponse:
    models: list[ModelInfo]
    supports_custom: bool
    error: str | None = None
    message: str | None = None
```

### Registry

```python
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
            ModelInfo(id="openrouter/anthropic/claude-sonnet-4-20250514", label="Claude Sonnet 4"),
            ModelInfo(id="openrouter/meta-llama/llama-3.3-70b-instruct", label="Llama 3.3 70B"),
            ModelInfo(id="openrouter/google/gemini-2.5-flash-preview", label="Gemini 2.5 Flash"),
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
```

### Functions

- `get_provider(name: str) -> ProviderConfig | None` — lookup by key.
- `async fetch_models(provider_name: str, base_url: str | None) -> ModelListResponse` —
  for LOCAL providers, hits `{base_url}{models_endpoint}` with 3s timeout. Ollama
  parses `/api/tags` response (`models[].name`), LM Studio parses `/v1/models`
  (`data[].id`). Model IDs are prefixed with the provider name
  (`ollama/llama3.2`). For CLOUD providers, returns curated list from the registry.
  For CUSTOM, returns empty list with `supports_custom=True`.
- `get_configured_providers(db_path: Path) -> list[dict]` — checks credentials
  (via `get_credential`) and base_url (via DB settings) for each provider. Returns
  list of `{"name": str, "label": str, "configured": bool}`. Replaces the provider
  iteration logic currently in `_get_providers_status`. The existing
  `GET /api/settings/providers` JSON endpoint preserves its current response shape
  (including `has_key`, `base_url`, `keyring_unavailable`) by reading from the
  registry and augmenting with the same credential/settings checks — its contract
  does not change.
- `migrate_default_model(db_path: Path) -> None` — one-time migration (see section 5).

---

## 2. API Endpoint (`GET /api/providers/{name}/models`)

Returns an HTML partial (rendered from `model_area.html`) for HTMX consumption.

### Response States

| State | HTTP | Body |
|---|---|---|
| Enumerated (local, fetch succeeded) | 200 | Model `<select>` with fetched models + custom input |
| Curated + custom (cloud) | 200 | Model `<select>` with curated list + custom input |
| Reachable but empty (local, no models) | 200 | Warning message + custom input fallback |
| Unreachable (local, fetch failed) | 200 | Error message + Settings link + custom input fallback |
| Unknown provider | 404 | Error text |
| Provider not configured | 400 | Error text + Settings link |

All states return HTML. The model area partial handles all four success/warning/error
states via conditionals on the `ModelListResponse` fields — no special-casing in the
template.

States 3 and 4 (empty/unreachable) still render the custom model ID free-text input
as a fallback since all providers have `supports_custom=True`. The inline messages are
informational guidance. Launch is enabled as soon as the user types a model ID.

---

## 3. HTMX Selector Component

### Template Files

- **`templates/partials/model_selector.html`** — the full two-step selector. Contains
  the provider `<select>` and the model area container div. Used via
  `{% set selector_id = "assess" %}{% include "partials/model_selector.html" %}`.
  The `selector_id` variable scopes DOM IDs to avoid collisions when multiple
  selectors are on the same page.

- **`templates/partials/model_area.html`** — rendered server-side by the
  `/api/providers/{name}/models` route. Contains the model `<select>`, custom
  free-text `<input>`, refresh button, and inline messages. Handles all response
  states with the same template.

### HTMX Flow

1. Provider `<select>` has an `onchange` handler that calls
   `htmx.ajax("GET", "/api/providers/" + value + "/models", {target: "#" + selectorId + "-model-area"})`.
   This matches the existing `htmx.ajax()` pattern used in `providers_section.html`
   and `run_history.html`.
2. The endpoint renders `model_area.html` with the `ModelListResponse` data and
   returns the HTML fragment.
3. HTMX swaps the fragment into the `#{selector_id}-model-area` div.
4. Refresh button calls the same `htmx.ajax()` to re-fetch.

### Initial Page Load with Defaults

When `default_provider` and `default_model_id` are set in settings:

1. The launcher route passes `default_provider` and `default_model_id` into the
   template context (no server-side model fetch during page render).
2. `model_selector.html` renders the provider `<select>` with `default_provider`
   pre-selected.
3. On `DOMContentLoaded`, an inline script checks each selector for a pre-selected
   provider. If one is found, it fires the same `htmx.ajax()` call that `onchange`
   uses — populating the model area asynchronously.
4. The model area shows a spinner during fetch, then resolves to the normal loaded
   state.
5. The endpoint response includes `default_model_id` as a query parameter
   (`?default=ollama/qwen2.5-7b`) so the rendered `model_area.html` can pre-select
   the matching model in the `<select>`. If `default_model_id` is not in the
   fetched list, the model field is left unselected — no silent fallback.

This approach keeps the launcher route fast (no blocking fetch on render), uses the
same fetch path for both default pre-selection and manual provider change, and shows
consistent UI states (spinner → loaded/error) regardless of how the fetch was
triggered. If the provider is offline, the user sees the same error state on page
load as they would after manually selecting it.

### Loading State

While the fetch is in flight, a spinner is shown in the model area via
`htmx-indicator` class on the model area container.

### Custom Model Input

When "Custom model id..." is selected from the model dropdown, a text input appears
below. JS toggles which element carries `name="model"` — only one is active at submit
time. The select is disabled (name removed) when custom input is active, and vice
versa. Only one `model` field is ever present in FormData.

### Form Submission

The launcher form submits `provider` and `model` as two separate fields. The model
field value is already a litellm-ready string (prefixed with provider name for both
fetched and curated models). For custom free-text entry, the user types the full
model identifier.

### Selector States (UI)

1. **Initial** — no provider selected. Model dropdown disabled, shows "Select a
   provider first".
2. **Loading** — provider selected, fetch in flight. Spinner in model area.
3. **Models loaded (local)** — model `<select>` populated + refresh button +
   custom input option.
4. **Models loaded (cloud)** — curated model `<select>` + custom input hint.
5. **Empty (local)** — warning message ("No models loaded...") + refresh button +
   custom input fallback.
6. **Unreachable (local)** — error message + Settings link + retry button +
   custom input fallback.

### Shared Usage

The selector is included identically in:
- Launcher: Assess form (`selector_id="assess"`)
- Launcher: Trace Path form (`selector_id="trace_path"`)
- Launcher: Campaign modal (`selector_id="campaign"`)
- Settings: Defaults section (`selector_id="defaults"`)

---

## 4. Settings Migration

### Schema Change

`default_model` (single setting) is replaced by:
- `default_provider` — provider key (e.g. `"ollama"`)
- `default_model_id` — litellm model string (e.g. `"ollama/qwen2.5-7b"`)

### Migration Logic (`migrate_default_model`)

Called on first read of defaults in the settings GET and launcher GET routes.
Idempotent — safe to call multiple times.

1. Read `default_model` from DB settings.
2. If absent or empty, no migration needed.
3. If `default_provider` already exists, already migrated — delete `default_model`.
4. Split `default_model` on first `/`.
5. If left side is a key in `PROVIDERS`: write `default_provider` and
   `default_model_id`, delete `default_model`.
6. Otherwise (unknown prefix, missing slash, blank model id):
   delete `default_model`, log warning. User re-selects defaults.

Note: values with multiple slashes (e.g. `openrouter/anthropic/claude-sonnet-4`)
are valid — the split on first `/` yields a known provider (`openrouter`) and
the full original value is preserved as `default_model_id`.

### Defaults Endpoint Changes

- `POST /api/settings/defaults` accepts `default_provider` + `default_model_id`.
  The old `default_model` key is rejected.
- `GET /api/settings/defaults` returns `default_provider` + `default_model_id`.
- Settings defaults section uses the same HTMX selector component, saving without
  reachability validation. Validation happens at launch time.

---

## 5. Launch-Time Backend Validation

Added to `launch_workflow` in `routes.py`, before run creation. Replaces the existing
`_check_provider_credential` which only checks credential presence.

Validation steps:

1. Extract `provider` and `model` from submitted form fields.
2. Provider is in `PROVIDERS` registry → 400 "Unknown provider" if not.
3. Provider is configured (credential or base_url present) → 400 "Provider not
   configured" if not.
4. Model string is non-empty → 400 "No model selected" if empty.
5. For LOCAL providers: call `fetch_models()` (3s timeout) to confirm reachability.
   If unreachable → 400 with connection error details.
6. For CLOUD providers with `supports_custom=True`: accept any non-empty model
   string. No enumeration check.
7. All checks pass → compose litellm runtime string and proceed with run creation.

Step 5 reuses `fetch_models()` — a single call that confirms both reachability and
that the provider API is responding. No separate HEAD or health check request.

Error responses use the existing `launch_workflow` JSON error format so launcher JS
error handling works unchanged.

---

## 6. Test Strategy

### Existing Tests to Update

- **`test_templates.py`** — update assertions for the provider `<select>` and model
  area container instead of old `model_options` rendering.
- **`test_launcher_phase2.py`** — `TestModelOptions`: assert provider dropdown lists
  configured providers. `TestLauncherDefaults`: check `default_provider` and
  `default_model_id` in context.
- **`test_settings.py`** — `TestSaveDefaults`: POST `default_provider` +
  `default_model_id`. Add GET assertion for new fields.
- **`test_launch.py`** — `TestLaunchValidation`: add cases for unknown provider,
  unconfigured provider, empty model, unreachable local provider.
  `TestProviderGate`: update if it references old validation.
- **`test_run_correctness.py`** — update launch payloads to include `provider` field.

### New Tests

- **`tests/core/test_providers.py`** — new file:
  - `TestProviderRegistry`: `get_provider()` returns config for known names, `None`
    for unknown.
  - `TestFetchModels`: mock HTTP for Ollama/LM Studio. All 4 response states
    (enumerated, curated, empty, unreachable). 3s timeout behavior. Model ID
    prefixing.
  - `TestMigrateDefaultModel`: happy path, missing slash, blank model id, extra
    delimiters, unknown provider prefix, already migrated, `default_model` absent.

- **`tests/server/test_model_selector.py`** — new file:
  - `TestProviderModelsEndpoint`: `GET /api/providers/{name}/models` with mocked
    `fetch_models`. HTML partial response for each state. 404 for unknown provider,
    400 for unconfigured.
  - `TestLaunchValidation`: end-to-end launch with invalid provider/model pairs,
    confirm rejection before run creation.

### Test Scope

Scoped to new/changed code per the brief. Full suite is run by the developer at PR
review.

---

## 7. Architecture.md Update

### New Section: "Provider Registry"

Documents `src/q_ai/core/providers.py` — the `PROVIDERS` dict, `ProviderType` enum,
dataclasses, `fetch_models()`. Notes this is the single source of truth for provider
definitions.

### Updated Section: "LLM Abstraction"

Notes that the `provider/model` litellm string is composed at launch time from
separate fields, not stored as a single string.

### Updated Section: "Settings"

Documents `default_provider` + `default_model_id` replacing `default_model`.
Documents the automatic migration.

### New Section: "Provider/Model Selector"

Documents the HTMX component (`model_selector.html` + `model_area.html`), the
`GET /api/providers/{name}/models` endpoint and its response states, and the shared
usage via `{% include %}` with `selector_id` scoping.

---

## Files Changed

| File | Action | Purpose |
|---|---|---|
| `src/q_ai/core/providers.py` | Create | Provider registry, model fetching, migration |
| `src/q_ai/server/routes.py` | Modify | New endpoint, launch validation, remove old model logic |
| `src/q_ai/server/templates/launcher.html` | Modify | Replace `model_dropdown()` with selector include |
| `src/q_ai/server/templates/partials/model_selector.html` | Create | Two-step selector partial |
| `src/q_ai/server/templates/partials/model_area.html` | Create | Model area partial (HTMX response) |
| `src/q_ai/server/templates/partials/defaults_section.html` | Modify | Use selector, split fields |
| `src/q_ai/server/templates/partials/providers_section.html` | Modify | Use PROVIDERS registry |
| `docs/Architecture.md` | Modify | Document new components |
| `tests/core/test_providers.py` | Create | Registry, fetch, migration tests |
| `tests/server/test_model_selector.py` | Create | Endpoint and selector tests |
| `tests/server/test_templates.py` | Modify | Update assertions |
| `tests/server/test_launcher_phase2.py` | Modify | Update assertions |
| `tests/server/test_settings.py` | Modify | Update defaults tests |
| `tests/server/test_launch.py` | Modify | Add validation cases |
| `tests/server/test_run_correctness.py` | Modify | Update launch payloads |

---

## Design Decisions

| Decision | Rationale |
|---|---|
| Curated model lists are minimal (2-3 per cloud provider) | Covers 90% of use cases; custom input handles the rest |
| `supports_custom=True` for all providers | Ensures escape hatch when enumeration fails for local providers |
| `ProviderType.CUSTOM` as third type | Custom endpoints may be local or cloud; distinct handling |
| Model IDs use `provider/model` format | Litellm-ready strings; no composition needed at submit time |
| 3s timeout for local model fetch | Localhost APIs respond in ms when healthy; fast fail for broken state |
| Migration discards unrecognizable legacy values | Clean break; no half-migrated state |
| Settings save without reachability validation | Validation at launch time is the hard gate; avoids redundant complexity |
| Endpoint always returns HTML partial | Matches codebase HTMX patterns; no content negotiation needed |
| `htmx.ajax()` for dynamic URL | Matches existing pattern in providers_section.html and run_history.html |
| `selector_id` via `{% set %}` before `{% include %}` | Clean Jinja2 scope inheritance; no macro complexity |
| Client-side auto-fetch for default pre-selection | Keeps launcher route fast; consistent UI states for all fetch triggers |
| Launch enabled with custom input even when empty/unreachable | Diverges from brief's "Launch disabled" criteria — `supports_custom=True` for all providers means the custom fallback is always available. Launch is enabled once a model ID is typed. Brief acceptance criteria should be updated to reflect this decision. |
