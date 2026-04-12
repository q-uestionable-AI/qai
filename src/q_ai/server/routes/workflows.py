"""Launcher page and workflow-control route handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from q_ai.audit.scanner.registry import list_scanner_names
from q_ai.core.config import get_credential
from q_ai.core.db import (
    create_target,
    get_connection,
    get_setting,
)
from q_ai.core.models import RunStatus
from q_ai.core.providers import (
    ProviderType,
    fetch_models,
    get_configured_providers,
    get_provider,
)
from q_ai.cxp.formats import list_formats as list_cxp_formats
from q_ai.inject.models import InjectionTechnique
from q_ai.orchestrator.registry import get_workflow, list_workflows
from q_ai.orchestrator.runner import WorkflowRunner
from q_ai.server.routes._shared import (
    _detect_local_ip,
    _get_db_path,
    _get_templates,
    logger,
)
from q_ai.services import run_service

router = APIRouter()

_VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}
_background_tasks: set[asyncio.Task[None]] = set()

_QUICK_ACTIONS = {"scan", "intercept", "campaign"}

_QUICK_ACTION_PROVIDER_REQUIRED = {"campaign"}

_QUICK_ACTION_WORKFLOW_MAP = {
    "scan": "qa_scan",
    "intercept": "qa_intercept",
    "campaign": "qa_campaign",
}


def _load_launcher_db_context(db_path: Path | None) -> dict[str, Any]:
    """Load provider list and launcher defaults (blocking DB reads)."""
    all_providers = get_configured_providers(db_path)
    with get_connection(db_path) as conn:
        default_transport = get_setting(conn, "audit.default_transport") or "stdio"
        saved_callback_url = get_setting(conn, "ipi.default_callback_url") or ""
    return {
        "providers": [p for p in all_providers if p["configured"]],
        "default_transport": default_transport,
        "saved_callback_url": saved_callback_url,
    }


@router.get("/launcher")
async def launcher(request: Request) -> HTMLResponse:
    """Render the workflow launcher page."""
    templates = _get_templates(request)

    hero_workflow: dict[str, Any] | None = None
    workflows: list[dict[str, Any]] = []
    for wf in list_workflows():
        if not wf.visible_in_launcher:
            continue
        entry = {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "modules": wf.modules,
            "implemented": wf.executor is not None,
            "requires_provider": wf.requires_provider,
        }
        if wf.is_hero:
            hero_workflow = entry
        else:
            workflows.append(entry)

    db_path = _get_db_path(request)
    db_ctx = await asyncio.to_thread(_load_launcher_db_context, db_path)
    defaults = {
        "ipi_callback_url": (
            db_ctx["saved_callback_url"] or f"http://{_detect_local_ip()}:8080/callback"
        ),
        "audit_default_transport": db_ctx["default_transport"],
    }

    return templates.TemplateResponse(
        request,
        "launcher.html",
        {
            "active": "launcher",
            "hero_workflow": hero_workflow,
            "workflows": workflows,
            "providers": db_ctx["providers"],
            "defaults": defaults,
            "injection_techniques": [
                {"value": t.value, "label": t.value.replace("_", " ").title()}
                for t in InjectionTechnique
            ],
            "scanner_categories": list_scanner_names(),
            "cxp_formats": list_cxp_formats(),
        },
    )


@router.get("/operations")
async def operations_redirect(request: Request) -> RedirectResponse:
    """Redirect /operations to /runs (backward compat for one release)."""
    url = "/runs"
    if request.url.query:
        url += f"?{request.url.query}"
    return RedirectResponse(url=url, status_code=301)


def _validate_transport_and_model(
    body: dict[str, Any],
) -> JSONResponse | None:
    """Validate transport, command/url, and model fields from a workflow body.

    Args:
        body: The parsed request body dict.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    transport = body.get("transport", "").strip()
    if transport not in _VALID_TRANSPORTS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    f"Invalid transport. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
                ),
            },
        )

    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None

    if transport == "stdio" and not command:
        return JSONResponse(
            status_code=422,
            content={"detail": "command is required for stdio transport"},
        )
    if transport in ("sse", "streamable-http") and not url:
        return JSONResponse(
            status_code=422,
            content={"detail": "url is required for sse/streamable-http transport"},
        )

    model = body.get("model", "").strip()
    if not model or "/" not in model:
        return JSONResponse(
            status_code=422,
            content={"detail": "model must be non-empty and in provider/model format"},
        )

    return None


def _build_assess_config(body: dict[str, Any], target_id: str) -> dict[str, Any] | JSONResponse:
    """Build config for the assess workflow."""
    error = _validate_transport_and_model(body)
    if error is not None:
        return error

    transport = body.get("transport", "").strip()
    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None
    model = body.get("model", "").strip()

    raw_rounds = body.get("rounds", 1)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer"},
        )
    rounds = raw_rounds
    if not 1 <= rounds <= 10:
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer between 1 and 10"},
        )
    rxp_enabled = bool(body.get("rxp_enabled", False))

    # Extract inject technique filter (None = not specified, [] = none selected)
    raw_techniques = body.get("techniques")
    techniques: list[str] | None = None
    if isinstance(raw_techniques, list):
        techniques = [str(t) for t in raw_techniques]

    # Extract explicit payload name filter (overrides techniques when set)
    raw_payloads = body.get("payload_names")
    payloads: list[str] | None = None
    if isinstance(raw_payloads, list):
        payloads = [str(p) for p in raw_payloads]

    # Extract audit category filter (None = not specified, [] = none selected)
    raw_checks = body.get("checks")
    checks: list[str] | None = None
    if isinstance(raw_checks, list):
        checks = [str(c) for c in raw_checks]

    return {
        "target_id": target_id,
        "transport": transport,
        "command": command,
        "url": url,
        "rxp_enabled": rxp_enabled,
        "audit": {"checks": checks},
        "inject": {
            "model": model,
            "rounds": rounds,
            "techniques": techniques,
            "payloads": payloads,
        },
        "proxy": {"intercept": False},
    }


def _build_test_docs_config(body: dict[str, Any], target_id: str) -> dict[str, Any] | JSONResponse:
    """Build config for the test_docs workflow."""
    callback_url = body.get("callback_url", "").strip()
    if not callback_url:
        return JSONResponse(
            status_code=422,
            content={"detail": "callback_url is required"},
        )
    return {
        "target_id": target_id,
        "callback_url": callback_url,
        "output_dir": "",
        "format": body.get("format", "pdf"),
        "payload_style": body.get("payload_style", "obvious"),
        "payload_type": body.get("payload_type", "callback"),
        "base_name": "report",
        "rxp_enabled": bool(body.get("rxp_enabled", False)),
        "rxp": {
            "model_id": body.get("rxp_model_id", ""),
            "profile_id": body.get("rxp_profile_id") or None,
            "target_id": target_id,
        },
    }


def _build_test_assistant_config(
    body: dict[str, Any], target_id: str
) -> dict[str, Any] | JSONResponse:
    """Build config for the test_assistant workflow."""
    format_id = body.get("format_id", "").strip()
    if not format_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "format_id is required"},
        )
    return {
        "target_id": target_id,
        "format_id": format_id,
        "rule_ids": body.get("rule_ids") or None,
        "output_dir": "",
        "repo_name": body.get("repo_name", "").strip() or None,
    }


def _build_trace_path_config(
    body: dict[str, Any], target_id: str, db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Build config for the trace_path workflow."""
    from q_ai.chain.loader import discover_chains, load_chain

    template_id = body.get("chain_template_id", "").strip()
    if not template_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "chain_template_id is required"},
        )

    # Resolve template id to absolute path
    chain_file: str | None = None
    try:
        for path in discover_chains():
            chain = load_chain(path)
            if chain.id == template_id:
                chain_file = str(path.resolve())
                break
    except Exception:
        logger.debug("Failed to load chain templates", exc_info=True)

    if chain_file is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Chain template not found: {template_id}"},
        )

    # Validate transport and model (same rules as assess)
    error = _validate_transport_and_model(body)
    if error is not None:
        return error

    transport = body.get("transport", "").strip()
    command = body.get("command", "").strip() or None
    url = body.get("url", "").strip() or None
    model = body.get("model", "").strip()

    return {
        "target_id": target_id,
        "chain_file": chain_file,
        "transport": transport,
        "command": command,
        "url": url,
        "inject_model": model,
    }


def _build_blast_radius_config(
    body: dict[str, Any], db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Build config for the blast_radius workflow.

    Derives target_id from the chain execution's run row instead of
    creating a new target.
    """
    exec_id = body.get("chain_execution_id", "").strip()
    if not exec_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "chain_execution_id is required"},
        )
    with get_connection(db_path) as conn:
        exec_row = conn.execute(
            "SELECT ce.id, r.target_id FROM chain_executions ce "
            "JOIN runs r ON r.id = ce.run_id WHERE ce.id = ?",
            (exec_id,),
        ).fetchone()
    if exec_row is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Chain execution not found"},
        )
    target_id = exec_row["target_id"]
    return {
        "target_id": target_id,
        "chain_execution_id": exec_id,
    }


def _build_generate_report_config(
    body: dict[str, Any], db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Build config for the generate_report workflow.

    Validates that the target_id exists in the database.
    """
    target_id = body.get("target_id", "").strip()
    if not target_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "target_id is required"},
        )
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT id FROM targets WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Target not found"},
        )
    return {
        "target_id": target_id,
        "from_date": body.get("from_date") or None,
        "to_date": body.get("to_date") or None,
        "include_evidence_pack": bool(body.get("include_evidence_pack", False)),
    }


async def _run_workflow(
    runner: WorkflowRunner,
    executor: Any,
    config: dict[str, Any],
) -> None:
    """Execute a workflow in the background, handling unexpected failures.

    Args:
        runner: The active WorkflowRunner instance.
        executor: Async executor function from the workflow registry.
        config: Workflow configuration dict.
    """
    try:
        await executor(runner, config)
    except Exception as exc:
        # Only fail if not already in terminal status

        def _fetch_run() -> Any:
            with get_connection(runner._db_path) as conn:
                return run_service.get_run(conn, runner.run_id)

        run = await asyncio.to_thread(_fetch_run)
        if run and run.status in (RunStatus.RUNNING, RunStatus.PENDING):
            await runner.fail(error=str(exc))


def _read_provider_config(provider_name: str, db_path: Path | None) -> tuple[str | None, str]:
    """Read (credential, base_url) for a provider (blocking DB + keyring)."""
    try:
        cred_value = get_credential(provider_name)
    except RuntimeError:
        cred_value = None
    with get_connection(db_path) as conn:
        base_url_value = get_setting(conn, f"{provider_name}.base_url") or ""
    return cred_value, base_url_value


async def _validate_provider_model(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | None:
    """Validate provider/model pair before launch.

    Checks: provider is known, configured, model is non-empty, and
    local providers are reachable.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    provider_name = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()

    if not provider_name and model and "/" in model:
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

    cred, base_url = await asyncio.to_thread(_read_provider_config, provider_name, db_path)

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


def _build_workflow_config(
    workflow_id: str, body: dict[str, Any], db_path: Path | None
) -> dict[str, Any] | JSONResponse:
    """Dispatch to the appropriate config builder for the given workflow.

    Args:
        workflow_id: The workflow identifier string.
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        The workflow config dict on success, or a JSONResponse with error details.
    """
    builders: dict[str, Callable[[], dict[str, Any] | JSONResponse]] = {
        "assess": partial(_build_assess_config, body, ""),
        "test_docs": partial(_build_test_docs_config, body, ""),
        "test_assistant": partial(_build_test_assistant_config, body, ""),
        "trace_path": partial(_build_trace_path_config, body, "", db_path),
        "blast_radius": partial(_build_blast_radius_config, body, db_path),
        "generate_report": partial(_build_generate_report_config, body, db_path),
    }
    builder = builders.get(workflow_id)
    if builder is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"No builder for: {workflow_id}"},
        )
    return builder()


def _apply_target_id(config: dict[str, Any], target_id: str) -> None:
    """Set target_id on the config and any nested sub-configs.

    Args:
        config: The workflow configuration dict (mutated in place).
        target_id: The newly created target identifier.
    """
    config["target_id"] = target_id
    if "rxp" in config and isinstance(config["rxp"], dict):
        config["rxp"]["target_id"] = target_id


def _prepare_output_dir(
    workflow_id: str, run_id: str, config: dict[str, Any]
) -> JSONResponse | None:
    """Create the artifact output directory for workflows that need one.

    Applies to test_docs, test_assistant, and generate_report workflows.
    Sets ``config["output_dir"]`` on success.

    Args:
        workflow_id: The workflow identifier string.
        run_id: The run identifier for directory naming.
        config: The workflow configuration dict (mutated in place).

    Returns:
        A JSONResponse with 500 status on failure, or None on success.
    """
    if workflow_id == "generate_report":
        output_dir = Path.home() / ".qai" / "exports" / "generate_report" / run_id
    elif workflow_id in ("test_docs", "test_assistant"):
        output_dir = Path.home() / ".qai" / "artifacts" / workflow_id / run_id
    else:
        return None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Failed to create output directory for %s", workflow_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to prepare artifact output directory"},
        )
    config["output_dir"] = str(output_dir)
    return None


@router.get("/api/inject/payloads")
async def api_inject_payloads(request: Request) -> JSONResponse:
    """Return all inject payload template metadata.

    Returns:
        JSONResponse with a list of payload template metadata objects,
        each containing name, technique, owasp_ids, and description.
    """
    from q_ai.inject.payloads.loader import load_all_templates

    templates = load_all_templates()
    payload_data = [
        {
            "name": t.name,
            "technique": t.technique.value,
            "owasp_ids": t.owasp_ids,
            "description": t.description,
        }
        for t in templates
    ]
    return JSONResponse(content=payload_data)


async def _parse_launch_body(request: Request) -> dict[str, Any] | JSONResponse:
    """Parse and validate the ``/api/workflows/launch`` request body."""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON structure"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON structure"})
    return body


_NO_TARGET_NAME_WORKFLOWS = frozenset({"blast_radius", "generate_report"})


async def _validate_launch_request(
    body: dict[str, Any], db_path: Path | None
) -> tuple[Any, str, str] | JSONResponse:
    """Validate workflow + provider + target_name from the launch body.

    Returns ``(entry, workflow_id, target_name)`` on success or a
    ``JSONResponse`` with the first validation error encountered.
    """
    workflow_id = body.get("workflow_id", "assess").strip()
    entry = get_workflow(workflow_id)
    if entry is None or entry.executor is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown workflow: {workflow_id}"},
        )
    if entry.requires_provider:
        validation_error = await _validate_provider_model(body, db_path)
        if validation_error is not None:
            return validation_error
    target_name = ""
    if workflow_id not in _NO_TARGET_NAME_WORKFLOWS:
        target_name = body.get("target_name", "").strip()
        if not target_name:
            return JSONResponse(
                status_code=422,
                content={"detail": "target_name is required"},
            )
    return entry, workflow_id, target_name


@router.post("/api/workflows/launch")
async def launch_workflow(request: Request) -> JSONResponse:
    """Launch a workflow.

    Validates the request, dispatches to per-workflow config building,
    creates a target (where applicable), and starts the workflow as a
    background task.
    """
    body = await _parse_launch_body(request)
    if isinstance(body, JSONResponse):
        return body
    db_path = _get_db_path(request)

    validation = await _validate_launch_request(body, db_path)
    if isinstance(validation, JSONResponse):
        return validation
    entry, workflow_id, target_name = validation

    # --- Build workflow config (before target creation to avoid orphan rows) ---
    # _build_workflow_config performs blocking DB reads for blast_radius /
    # generate_report; run it in a worker thread to keep the event loop free.
    result = await asyncio.to_thread(_build_workflow_config, workflow_id, body, db_path)
    if isinstance(result, JSONResponse):
        return result
    config: dict[str, Any] = result

    # --- Create target (only after builder succeeds, skip for existing-target workflows) ---
    if workflow_id not in _NO_TARGET_NAME_WORKFLOWS:

        def _create_target_sync() -> str:
            with get_connection(db_path) as conn:
                return create_target(conn, type="server", name=target_name)

        target_id = await asyncio.to_thread(_create_target_sync)
        _apply_target_id(config, target_id)

    # --- Create runner ---
    runner = WorkflowRunner(
        workflow_id=workflow_id,
        config=config,
        ws_manager=request.app.state.ws_manager,
        active_workflows=request.app.state.active_workflows,
        db_path=db_path,
        source="web",
    )

    # --- Set output_dir from run_id BEFORE start() persists config ---
    dir_error = _prepare_output_dir(workflow_id, runner.run_id, config)
    if dir_error is not None:
        return dir_error

    await runner.start()

    # --- Fire-and-forget background task ---
    task = asyncio.create_task(_run_workflow(runner, entry.executor, config))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        status_code=201,
        content={
            "run_id": runner.run_id,
            "redirect": f"/operations?run_id={runner.run_id}",
        },
    )


def _str_field(body: dict[str, Any], key: str, default: str = "") -> str:
    """Extract a string field from a request body, rejecting non-string values.

    Args:
        body: The parsed request body dict.
        key: The field name to extract.
        default: Default value if key is missing or None.

    Returns:
        The stripped string value.

    Raises:
        TypeError: If the value is present but not a string (e.g. number,
            array, object).
    """
    val = body.get(key, default)
    if val is None:
        return default
    if not isinstance(val, str):
        raise TypeError(f"'{key}' must be a string")
    return val.strip()


def _validate_campaign_fields(body: dict[str, Any]) -> JSONResponse | None:
    """Validate campaign-specific fields (model and rounds).

    Args:
        body: The parsed request body dict.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    model = _str_field(body, "model")
    if not model or "/" not in model:
        return JSONResponse(
            status_code=422,
            content={"detail": "model must be non-empty and in provider/model format"},
        )
    raw_rounds = body.get("rounds", 1)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer"},
        )
    rounds = raw_rounds
    if not 1 <= rounds <= 10:
        return JSONResponse(
            status_code=422,
            content={"detail": "rounds must be an integer between 1 and 10"},
        )
    return None


async def _validate_quick_action(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | tuple[str, str]:
    """Validate quick action request fields.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        A JSONResponse on validation error, or a (action, target_name) tuple
        on success.
    """
    action = _str_field(body, "action")
    if action not in _QUICK_ACTIONS:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown action: {action}"},
        )

    if action in _QUICK_ACTION_PROVIDER_REQUIRED:
        validation_error = await _validate_provider_model(body, db_path)
        if validation_error is not None:
            return validation_error

    target_name = _str_field(body, "target_name")
    if not target_name:
        return JSONResponse(
            status_code=422,
            content={"detail": "target_name is required"},
        )

    transport_error = _validate_transport_and_command(body)
    if transport_error is not None:
        return transport_error

    if action == "campaign":
        campaign_error = _validate_campaign_fields(body)
        if campaign_error is not None:
            return campaign_error

    return action, target_name


def _validate_transport_and_command(body: dict[str, Any]) -> JSONResponse | None:
    """Validate transport and command/url fields from a request body.

    Args:
        body: The parsed request body dict.

    Returns:
        A JSONResponse with 422 status if validation fails, or None on success.
    """
    transport = _str_field(body, "transport")
    if transport not in _VALID_TRANSPORTS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    f"Invalid transport. Must be one of: {', '.join(sorted(_VALID_TRANSPORTS))}"
                ),
            },
        )
    command = _str_field(body, "command") or None
    url = _str_field(body, "url") or None
    if transport == "stdio" and not command:
        return JSONResponse(
            status_code=422,
            content={"detail": "command is required for stdio transport"},
        )
    if transport in ("sse", "streamable-http") and not url:
        return JSONResponse(
            status_code=422,
            content={"detail": "url is required for sse/streamable-http transport"},
        )
    return None


def _build_quick_action_config(action: str, body: dict[str, Any], target_id: str) -> dict[str, Any]:
    """Build config dict for a quick action.

    Args:
        action: The quick action name (scan, intercept, campaign).
        body: The parsed request body dict.
        target_id: The created target ID.

    Returns:
        Configuration dict for the quick action executor.
    """
    config: dict[str, Any] = {
        "target_id": target_id,
        "transport": _str_field(body, "transport"),
        "command": _str_field(body, "command") or None,
        "url": _str_field(body, "url") or None,
    }
    if action == "campaign":
        config["model"] = _str_field(body, "model")
        config["rounds"] = int(body.get("rounds", 1))
        raw_techniques = body.get("techniques")
        if isinstance(raw_techniques, list):
            config["techniques"] = [str(t) for t in raw_techniques]
    if action == "scan":
        raw_checks = body.get("checks")
        if isinstance(raw_checks, list):
            config["checks"] = [str(c) for c in raw_checks]
    return config


@router.post("/api/quick-actions/launch")
async def launch_quick_action(request: Request) -> JSONResponse:
    """Launch a single-module quick action.

    Validates the request body, creates a target and run, then executes
    the module operation in the background.

    Args:
        request: The incoming HTTP request with a JSON body containing
            action, target_name, transport, and action-specific fields.

    Returns:
        JSONResponse with 201 status containing run_id and redirect URL
        on success, or 400/422 on validation failure.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid JSON in request body"},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request body must be a JSON object"},
        )
    db_path = _get_db_path(request)

    try:
        result = await _validate_quick_action(body, db_path)
    except TypeError:
        logger.warning("Invalid request parameters in quick action", exc_info=True)
        return JSONResponse(status_code=422, content={"detail": "Invalid request parameters"})
    if isinstance(result, JSONResponse):
        return result
    action, target_name = result

    def _create_target_sync() -> str:
        with get_connection(db_path) as conn:
            return create_target(conn, type="server", name=target_name)

    target_id = await asyncio.to_thread(_create_target_sync)

    try:
        config = _build_quick_action_config(action, body, target_id)
    except TypeError:
        logger.warning("Invalid action configuration in quick action", exc_info=True)
        return JSONResponse(status_code=422, content={"detail": "Invalid action configuration"})

    runner = WorkflowRunner(
        workflow_id=_QUICK_ACTION_WORKFLOW_MAP[action],
        config=config,
        ws_manager=request.app.state.ws_manager,
        active_workflows=request.app.state.active_workflows,
        db_path=db_path,
        source="web",
    )
    await runner.start()

    from q_ai.orchestrator.workflows.quick_actions import (
        quick_campaign,
        quick_intercept,
        quick_scan,
    )

    executors = {
        "scan": quick_scan,
        "intercept": quick_intercept,
        "campaign": quick_campaign,
    }

    task = asyncio.create_task(_run_workflow(runner, executors[action], config))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        status_code=201,
        content={
            "run_id": runner.run_id,
            "redirect": f"/runs?run_id={runner.run_id}",
        },
    )


@router.post("/api/workflows/{run_id}/resume")
async def resume_workflow(request: Request, run_id: str) -> JSONResponse:
    """Resume a workflow that is waiting for user action.

    Looks up the active WorkflowRunner for this run_id and calls resume().
    Returns 404 if no active runner exists, 409 if the run is not in
    WAITING_FOR_USER state (idempotent — duplicate clicks are safe).
    """
    active_workflows: dict[str, object] = request.app.state.active_workflows
    runner = active_workflows.get(run_id)
    if runner is None:
        return JSONResponse(status_code=404, content={"detail": "No active workflow for this run"})

    if not isinstance(runner, WorkflowRunner):
        return JSONResponse(status_code=500, content={"detail": "Invalid runner type"})

    db_path = _get_db_path(request)

    def _fetch_run() -> Any:
        with get_connection(db_path) as conn:
            return run_service.get_run(conn, run_id)

    run = await asyncio.to_thread(_fetch_run)
    if run is None or run.status != RunStatus.WAITING_FOR_USER:
        return JSONResponse(
            status_code=409,
            content={"detail": "Workflow is not waiting for user action"},
        )

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        body: dict = {}
    else:
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid JSON in request body"},
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"detail": "Request body must be a JSON object"},
            )
    await runner.resume(body)
    return JSONResponse(content={"status": "resumed"})


def _sync_conclude(
    db_path: Path | None,
    run_id: str,
) -> str:
    """Run the conclude-campaign DB work synchronously.

    Returns:
        ``"not_found"``, ``"already_terminal"``, or ``"concluded"``.
    """
    with get_connection(db_path) as conn:
        return run_service.conclude_run(conn, run_id)


@router.post("/api/workflows/{run_id}/conclude")
async def conclude_campaign(request: Request, run_id: str) -> JSONResponse:
    """Conclude a research campaign, transitioning the run to COMPLETED.

    Marks the parent run and any children still in WAITING_FOR_USER as
    COMPLETED with finished_at. Idempotent: already-terminal runs return
    success. Emits a run_status WebSocket event and unblocks the runner's
    wait event so the adapter coroutine exits cleanly.
    """
    db_path = _get_db_path(request)
    result = await asyncio.to_thread(_sync_conclude, db_path, run_id)

    if result == "not_found":
        return JSONResponse(status_code=404, content={"detail": "Run not found"})
    if result == "already_terminal":
        return JSONResponse(content={"status": "concluded"})

    # Emit WebSocket event so UI updates live
    ws_manager = request.app.state.ws_manager
    await ws_manager.broadcast(
        {
            "type": "run_status",
            "run_id": run_id,
            "status": int(RunStatus.COMPLETED),
            "module": "workflow",
        }
    )

    # Unblock the runner's wait event so the adapter coroutine exits cleanly
    active_workflows: dict[str, object] = request.app.state.active_workflows
    runner = active_workflows.get(run_id)
    if runner is not None and isinstance(runner, WorkflowRunner):
        runner.unblock()
        active_workflows.pop(run_id, None)

    return JSONResponse(content={"status": "concluded"})


def _sync_conclude_stranded(db_path: Path | None, run_id: str) -> str:
    """Transition a ``WAITING_FOR_USER`` run to ``CANCELLED`` atomically.

    Returns:
        ``"not_found"`` if the run does not exist, ``"not_stranded"`` if
        its current status is not ``WAITING_FOR_USER``, or ``"cancelled"``
        on successful transition.
    """
    with get_connection(db_path) as conn:
        return run_service.conclude_stranded(conn, run_id)


@router.post("/api/runs/{run_id}/conclude-stranded")
async def api_conclude_stranded_run(request: Request, run_id: str) -> Response:
    """Conclude a stranded ``WAITING_FOR_USER`` run as ``CANCELLED``.

    Only operates on runs that (a) are in ``WAITING_FOR_USER`` status and
    (b) do not have an active ``WorkflowRunner`` in ``app.state``. The
    second check is defensive: a run with a live runner is not stranded,
    it is actively waiting, and the operator should resume or conclude it
    via the normal paths.

    On success the run is removed from ``app.state.stranded_runs`` and an
    empty ``HTMLResponse`` is returned so the HTMX-driven banner row can
    swap itself out.
    """
    import datetime as _dt

    active_workflows: dict[str, object] = request.app.state.active_workflows
    if run_id in active_workflows:
        return JSONResponse(
            status_code=409,
            content={"detail": "Run has an active runner; not stranded"},
        )

    db_path = _get_db_path(request)
    result = await asyncio.to_thread(_sync_conclude_stranded, db_path, run_id)

    if result == "not_found":
        return JSONResponse(status_code=404, content={"detail": "Run not found"})
    if result == "not_stranded":
        return JSONResponse(
            status_code=409,
            content={"detail": "Run is not in WAITING_FOR_USER status"},
        )

    stranded: dict[str, tuple[str | None, _dt.datetime | None]] = getattr(
        request.app.state, "stranded_runs", {}
    )
    stranded.pop(run_id, None)

    return HTMLResponse("")
