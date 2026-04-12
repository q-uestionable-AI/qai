"""Admin routes — provider CRUD, credentials, targets, defaults."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from q_ai.core.config import delete_credential, get_credential, set_credential
from q_ai.core.db import (
    get_connection,
    get_setting,
    list_targets,
    set_setting,
)
from q_ai.core.providers import (
    PROVIDERS,
    AuthStyle,
    ProviderType,
    fetch_models,
    get_provider,
)
from q_ai.server.routes._shared import (
    _get_db_path,
    _get_templates,
    logger,
)
from q_ai.services import db_service

router = APIRouter()

_ASSIST_PROVIDER_ORDER = [
    "anthropic",
    "google",
    "openai",
    "xai",
    "groq",
    "openrouter",
    "lmstudio",
    "ollama",
    "custom",
]


def _get_assist_provider_choices() -> list[dict[str, str | bool]]:
    """Build the provider list for the assistant selector.

    Returns all providers from the registry with name, label, type,
    and default_base_url — independent of target provider configuration.
    Providers are returned in a fixed display order.
    """
    return [
        {
            "name": name,
            "label": PROVIDERS[name].label,
            "type": PROVIDERS[name].type.value,
            "default_base_url": PROVIDERS[name].default_base_url or "",
            "has_models_endpoint": PROVIDERS[name].models_endpoint is not None,
        }
        for name in _ASSIST_PROVIDER_ORDER
        if name in PROVIDERS
    ]


def _get_providers_status(request: Request) -> list[dict[str, Any]]:
    """Build a list of provider statuses."""
    db_path = _get_db_path(request)
    result: list[dict[str, Any]] = []
    with get_connection(db_path) as conn:
        for name in PROVIDERS:
            keyring_unavailable = False
            try:
                cred = get_credential(name)
            except RuntimeError:
                cred = None
                keyring_unavailable = True
            base_url = get_setting(conn, f"{name}.base_url") or ""
            configured = cred is not None or bool(base_url)
            result.append(
                {
                    "name": name,
                    "label": PROVIDERS[name].label,
                    "configured": configured,
                    "has_key": cred is not None,
                    "base_url": base_url,
                    "keyring_unavailable": keyring_unavailable,
                }
            )
    return result


@router.get("/admin")
async def admin_page(request: Request) -> HTMLResponse:
    """Render the admin page."""
    templates = _get_templates(request)
    providers_status = _get_providers_status(request)
    db_path = _get_db_path(request)

    with get_connection(db_path) as conn:
        defaults = {
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
        }
        assist_base_url = get_setting(conn, "assist.base_url") or ""
        assist_provider = get_setting(conn, "assist.provider") or ""
        assist_model = get_setting(conn, "assist.model") or ""
        targets = list_targets(conn)

    assist_provider_label = ""
    if assist_provider and assist_provider in PROVIDERS:
        assist_provider_label = PROVIDERS[assist_provider].label

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "active": "admin",
            "providers_status": providers_status,
            "assist_providers": _get_assist_provider_choices(),
            "defaults": defaults,
            "assist_base_url": assist_base_url,
            "assist_provider": assist_provider,
            "assist_model": assist_model,
            "assist_provider_label": assist_provider_label,
            "targets": targets,
        },
    )


@router.get("/api/admin/providers")
async def api_list_providers(request: Request) -> JSONResponse:
    """List configured providers with status."""
    return JSONResponse(content={"providers": _get_providers_status(request)})


@router.post("/api/admin/providers")
async def api_add_provider(request: Request) -> JSONResponse:
    """Add a provider -- key to keyring, base_url to DB settings."""
    body = await request.json()
    provider = body.get("provider", "").strip().lower()
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()

    if not provider:
        return JSONResponse(
            status_code=422,
            content={"detail": "Provider name required"},
        )

    cloud_providers = {"anthropic", "google", "openai", "groq", "openrouter", "xai"}
    if provider in cloud_providers and not api_key:
        return JSONResponse(
            status_code=422,
            content={"detail": "API key required for cloud providers"},
        )

    if api_key:
        try:
            set_credential(provider, api_key)
        except RuntimeError:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": (
                        "Keyring unavailable — set credentials via environment variable instead."
                    ),
                },
            )
        except Exception:
            logger.exception("Failed to store credential for %s", provider)
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to store credential"},
            )

    if base_url:
        db_path = _get_db_path(request)
        with get_connection(db_path) as conn:
            set_setting(conn, f"{provider}.base_url", base_url)

    return JSONResponse(
        status_code=201,
        content={"status": "ok", "provider": provider},
    )


@router.delete("/api/admin/providers/{provider}")
async def api_delete_provider(request: Request, provider: str) -> JSONResponse:
    """Delete a provider -- remove from keyring and DB."""
    provider = provider.strip().lower()
    with contextlib.suppress(Exception):
        delete_credential(provider)

    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        set_setting(conn, f"{provider}.base_url", "")

    return JSONResponse(content={"status": "deleted"})


async def _test_local_provider(db_path: Path | None, provider: str) -> JSONResponse:
    """Test connectivity for a local provider (ollama, lmstudio, custom).

    Args:
        db_path: Path to the SQLite database.
        provider: The local provider identifier.

    Returns:
        JSONResponse with connectivity status or error details.
    """
    with get_connection(db_path) as conn:
        base_url = get_setting(conn, f"{provider}.base_url")

    default_urls = {
        "ollama": "http://localhost:11434",
        "lmstudio": "http://localhost:1234",
    }
    url = base_url or default_urls.get(provider, "")
    if not url:
        return JSONResponse(
            status_code=404,
            content={"detail": "No base URL configured"},
        )

    health_path = "/api/tags" if provider == "ollama" else "/v1/models"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(f"{url}{health_path}")
            if resp.status_code == 200:
                return JSONResponse(
                    content={"status": "ok", "message": "Connected"},
                )
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"HTTP {resp.status_code}",
                },
            )
    except Exception:
        logger.exception("Provider connectivity check failed for %s", provider)
        return JSONResponse(
            content={"status": "error", "message": "Connection check failed"},
        )


async def _ping_cloud_endpoint(provider: str, credential: str) -> dict[str, str]:
    """Hit a cloud provider's models endpoint and return a status dict.

    Args:
        provider: Provider key (e.g. "openai").
        credential: API key for authentication.

    Returns:
        Dict with "status" ("ok" or "error") and "message".
    """
    config = PROVIDERS[provider]
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if config.auth_style == AuthStyle.BEARER:
        headers["Authorization"] = f"Bearer {credential}"
    elif config.auth_style == AuthStyle.X_API_KEY:
        headers["x-api-key"] = credential
    elif config.auth_style == AuthStyle.QUERY_KEY:
        params["key"] = credential
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"

    endpoint = f"{config.default_base_url}{config.models_endpoint}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(endpoint, headers=headers, params=params)
    except Exception:
        logger.exception("Cloud connectivity check failed for %s", provider)
        return {"status": "error", "message": "Connection check failed"}

    if resp.status_code == 200:
        return {"status": "ok", "message": "Connected"}
    if resp.status_code in (401, 403):
        return {"status": "error", "message": "Auth failed — check API key"}
    return {"status": "error", "message": f"HTTP {resp.status_code}"}


async def _test_cloud_provider(provider: str) -> JSONResponse:
    """Test connectivity for a cloud provider by hitting its models endpoint.

    Args:
        provider: The cloud provider identifier.

    Returns:
        JSONResponse with connectivity status or error details.
    """
    try:
        credential = get_credential(provider)
    except RuntimeError:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Keyring unavailable — set credentials via environment variable instead."
                ),
            },
        )
    if credential is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Provider not configured"},
        )

    config = PROVIDERS.get(provider)
    if not config or not config.models_endpoint or not config.default_base_url:
        return JSONResponse(
            content={"status": "ok", "message": "Credential configured"},
        )

    result = await _ping_cloud_endpoint(provider, credential)
    return JSONResponse(content=result)


@router.get("/api/admin/providers/{provider}/test")
async def api_test_provider(request: Request, provider: str) -> JSONResponse:
    """Test provider connectivity with a minimal check."""
    local_providers = {"ollama", "lmstudio", "custom"}

    if provider in local_providers:
        return await _test_local_provider(_get_db_path(request), provider)

    return await _test_cloud_provider(provider)


@router.get("/api/admin/defaults")
async def api_get_defaults(request: Request) -> JSONResponse:
    """Get default settings."""
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        defaults = {
            "audit.default_transport": (get_setting(conn, "audit.default_transport") or "stdio"),
            "ipi.default_callback_url": (get_setting(conn, "ipi.default_callback_url") or ""),
            "assist.provider": (get_setting(conn, "assist.provider") or ""),
            "assist.model": (get_setting(conn, "assist.model") or ""),
            "assist.base_url": (get_setting(conn, "assist.base_url") or ""),
        }
    return JSONResponse(content=defaults)


@router.post("/api/admin/defaults")
async def api_save_defaults(request: Request) -> JSONResponse:
    """Save default settings to DB."""
    body = await request.json()
    db_path = _get_db_path(request)
    allowed_keys = (
        "audit.default_transport",
        "ipi.default_callback_url",
        "assist.provider",
        "assist.model",
        "assist.base_url",
    )
    with get_connection(db_path) as conn:
        for key in allowed_keys:
            value = body.get(key)
            if value is not None:
                set_setting(conn, key, str(value))
    return JSONResponse(content={"status": "saved"})


@router.post("/api/admin/assist/credential")
async def api_save_assist_credential(request: Request) -> JSONResponse:
    """Save an API key for the assistant's provider (namespaced keyring)."""
    body = await request.json()
    provider = body.get("provider", "").strip().lower()
    api_key = body.get("api_key", "").strip()

    if not provider:
        return JSONResponse(
            status_code=422,
            content={"detail": "Provider name required"},
        )
    if not api_key:
        return JSONResponse(
            status_code=422,
            content={"detail": "API key required"},
        )

    keyring_key = f"assist.{provider}"
    try:
        set_credential(keyring_key, api_key)
    except RuntimeError:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Keyring unavailable — set credentials via environment variable instead."
                ),
            },
        )
    except Exception:
        logger.exception("Failed to store assist credential for %s", provider)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to store credential"},
        )

    return JSONResponse(content={"status": "saved"})


@router.get("/api/providers/{name}/models")
async def api_provider_models(request: Request, name: str) -> Response:
    """Fetch models for a provider and return an HTML partial."""
    templates = _get_templates(request)
    config = get_provider(name)
    if config is None:
        return HTMLResponse(
            content="<div class='text-error text-sm'>Unknown provider</div>",
            status_code=404,
        )

    # Accept inline credentials from the setup/edit form so the model
    # fetch works before the user has saved their configuration.
    inline_api_key = request.headers.get("x-assist-api-key")
    inline_base_url = request.query_params.get("base_url")

    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        if inline_api_key is None:
            try:
                cred = get_credential(name)
            except RuntimeError:
                cred = None
        else:
            cred = inline_api_key
        base_url = inline_base_url or get_setting(conn, f"{name}.base_url") or ""

    configured = cred is not None or bool(base_url)
    if not configured and config.type not in {ProviderType.CUSTOM, ProviderType.CLOUD}:
        return HTMLResponse(
            content=(
                "<div class='text-error text-sm'>Provider not configured. "
                "<a href='/settings#providers' class='link'>Settings</a></div>"
            ),
            status_code=400,
        )

    result = await fetch_models(name, base_url or None, api_key=cred)

    selector_id = request.query_params.get("selector_id", "default")
    default_model_id = request.query_params.get("default", "")

    return templates.TemplateResponse(
        request,
        "partials/model_area.html",
        {
            "models": result.models,
            "supports_custom": result.supports_custom,
            "error": result.error,
            "error_hint": result.error_hint,
            "message": result.message,
            "selector_id": selector_id,
            "provider_name": name,
            "default_model_id": default_model_id,
            "provider_type": config.type.value,
        },
    )


@router.get("/api/targets/list")
async def api_targets_list(request: Request) -> JSONResponse:
    """Return registered targets for the launcher dropdown."""
    db_path = _get_db_path(request)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, type, uri FROM targets ORDER BY created_at DESC"
        ).fetchall()
    return JSONResponse(content={"targets": [dict(r) for r in rows]})


def _check_target_name_exists(db_path: Path | None, name: str) -> bool:
    """Check whether a target with the given name exists in the database.

    Args:
        db_path: Path to the SQLite database.
        name: Target name to look up.

    Returns:
        True if a target with the name exists, False otherwise.
    """
    normalized = name.strip()
    if not normalized:
        return False
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM targets WHERE name = ? LIMIT 1",
            (normalized,),
        ).fetchone()
    return row is not None


@router.get("/api/targets/check-name")
async def api_check_target_name(
    request: Request,
    name: str = Query(...),
) -> JSONResponse:
    """Check if a target with the given name already exists.

    Runs the database lookup off the event loop via ``asyncio.to_thread``
    to avoid blocking on synchronous SQLite I/O.

    Args:
        request: The incoming HTTP request.
        name: Target name to check (query parameter).

    Returns:
        JSONResponse with ``{"exists": true}`` or ``{"exists": false}``.
    """
    normalized = name.strip()
    if not normalized:
        return JSONResponse(
            status_code=422,
            content={"detail": "name is required"},
        )
    db_path = _get_db_path(request)
    exists = await asyncio.to_thread(_check_target_name_exists, db_path, normalized)
    return JSONResponse(content={"exists": exists})


def _sync_delete_target(db_path: Path | None, target_id: str) -> int:
    """Delete a target and orphan its runs (blocking).

    Args:
        db_path: Path to the SQLite database.
        target_id: Full UUID of the target.

    Returns:
        Count of orphaned runs.

    Raises:
        ValueError: If target_id does not exist.
    """
    with get_connection(db_path) as conn:
        return db_service.delete_target(conn, target_id)


@router.delete("/api/targets/{target_id}", response_model=None)
async def api_delete_target(request: Request, target_id: str) -> JSONResponse:
    """Delete a target and orphan its associated runs.

    Args:
        request: The incoming HTTP request.
        target_id: Full UUID of the target to delete.

    Returns:
        JSON with status and orphaned_runs count, or 404.
    """
    db_path = _get_db_path(request)
    try:
        orphaned = await asyncio.to_thread(_sync_delete_target, db_path, target_id)
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "Target not found"})
    return JSONResponse(content={"status": "deleted", "orphaned_runs": orphaned})
