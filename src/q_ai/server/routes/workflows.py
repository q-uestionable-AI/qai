"""Launcher page and workflow-control route handlers.

Route handlers here are thin transport adapters: they parse requests,
delegate to :mod:`q_ai.services.workflow_service` for validation and
config assembly, then map service results to HTTP responses. Product
logic lives in the service layer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from q_ai.audit.scanner.registry import list_scanner_names
from q_ai.core.db import (
    create_target,
    get_connection,
    get_setting,
)
from q_ai.core.models import RunStatus
from q_ai.core.providers import get_configured_providers
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
from q_ai.services.managed_listener import ListenerState
from q_ai.services.workflow_service import (
    WorkflowValidationError,
    build_quick_action_config,
    build_workflow_config,
    validate_campaign_fields,
    validate_provider_model,
    validate_transport_and_command,
)

router = APIRouter()

_background_tasks: set[asyncio.Task[None]] = set()

_QUICK_ACTIONS = {"scan", "intercept", "campaign"}

_QUICK_ACTION_PROVIDER_REQUIRED = {"campaign"}

_QUICK_ACTION_WORKFLOW_MAP = {
    "scan": "qa_scan",
    "intercept": "qa_intercept",
    "campaign": "qa_campaign",
}

_NO_TARGET_NAME_WORKFLOWS = frozenset({"blast_radius", "generate_report"})


def _validation_error_response(exc: WorkflowValidationError) -> JSONResponse:
    """Translate a validation error into a 422 JSONResponse."""
    return JSONResponse(status_code=422, content={"detail": exc.detail})


def _lookup_or_create_server_target(db_path: Path | None, target_name: str) -> str:
    """Return the id of a ``type='server'`` target by name, creating it if absent.

    Phase 5 closes the duplicate-row loophole opened by
    :func:`launch_workflow` and :func:`launch_quick_action` each calling
    :func:`create_target` unconditionally on every submit. Free-text
    ``target_name`` inputs share the same name across repeat submits
    (same host, same model, same sweep), so unconditional inserts
    produce one target per submit — per the Phase 4 Risk #4 note and
    RFC Design Decision 3.

    Lookup is constrained to ``type='server'`` to match the type
    passed to :func:`create_target` here — avoids colliding against
    synthetic Unbound (``type='virtual'``) or endpoint targets that
    happen to share the name.

    Blocking SQLite; callers wrap in ``asyncio.to_thread``.

    Args:
        db_path: Path to the SQLite database, or ``None`` for default.
        target_name: Human-readable target name from the launcher form.

    Returns:
        The hex UUID of the existing or newly created target.
    """
    with get_connection(db_path) as conn:
        # BEGIN IMMEDIATE acquires a RESERVED lock before the SELECT so
        # two concurrent launches with the same target_name cannot both
        # observe an empty lookup and both INSERT (PR #131 review
        # Issue #2). The lock holds through the INSERT and releases on
        # the context-manager commit. Python's default isolation_level
        # does not auto-begin on SELECT, so this explicit BEGIN does
        # not conflict with the module's implicit-transaction handling.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM targets WHERE type = 'server' AND name = ? LIMIT 1",
            (target_name,),
        ).fetchone()
        if row is not None:
            existing_id: str = row["id"]
            return existing_id
        return create_target(conn, type="server", name=target_name)


async def _call_provider_validator(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | None:
    """Run :func:`validate_provider_model` and convert failures to 422.

    Returns ``None`` on success or a 422 ``JSONResponse`` on validation
    failure. Catches ``AttributeError`` and ``TypeError`` raised when
    the provider/model fields hold non-string types so a malformed body
    surfaces as 422 rather than escaping as 500.
    """
    try:
        await validate_provider_model(body, db_path)
    except WorkflowValidationError as exc:
        return _validation_error_response(exc)
    except (AttributeError, TypeError):
        return JSONResponse(status_code=422, content={"detail": "Invalid provider or model"})
    return None


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
    """Render the workflow launcher page.

    Accepts optional ``target_name`` and ``template`` query parameters
    used by the Intel detail page's "Generate with recommended template"
    affordance to prefill the test_docs form. Empty strings are treated
    as absent. The launcher does not resolve template selection itself;
    it consumes an already-resolved pair (per RFC Decision 5).
    """
    templates = _get_templates(request)

    prefill_target_name = request.query_params.get("target_name", "").strip() or None
    prefill_template_id = request.query_params.get("template", "").strip() or None
    prefill = {
        "target_name": prefill_target_name,
        "template_id": prefill_template_id,
    }

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

    # Tunnel-toggle context: surface at most one active handle (running or
    # adopted) and the foreign-listener record if present. The template
    # uses these to pick initial toggle state and render the inline badge.
    managed_listeners = request.app.state.managed_listeners
    active_managed_handle = next(
        (
            h
            for h in managed_listeners.values()
            if h.state in (ListenerState.RUNNING, ListenerState.ADOPTED)
        ),
        None,
    )
    foreign_listener = request.app.state.foreign_listener

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
            "active_managed_handle": active_managed_handle,
            "foreign_listener": foreign_listener,
            "prefill": prefill,
        },
    )


@router.get("/operations")
async def operations_redirect(request: Request) -> RedirectResponse:
    """Redirect /operations to /runs (backward compat for one release)."""
    url = "/runs"
    if request.url.query:
        url += f"?{request.url.query}"
    return RedirectResponse(url=url, status_code=301)


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
        provider_error = await _call_provider_validator(body, db_path)
        if provider_error is not None:
            return provider_error
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
    # build_workflow_config performs blocking DB reads for blast_radius /
    # generate_report; run it in a worker thread to keep the event loop free.
    def _build_config() -> dict[str, Any]:
        return build_workflow_config(workflow_id, body, db_path)

    try:
        config = await asyncio.to_thread(_build_config)
    except WorkflowValidationError as exc:
        return _validation_error_response(exc)

    # --- Create target (only after builder succeeds, skip for existing-target workflows) ---
    if workflow_id not in _NO_TARGET_NAME_WORKFLOWS:
        target_id = await asyncio.to_thread(_lookup_or_create_server_target, db_path, target_name)
        _apply_target_id(config, target_id)

    # --- Create runner ---
    runner = WorkflowRunner(
        workflow_id=workflow_id,
        config=config,
        ws_manager=request.app.state.ws_manager,
        active_workflows=request.app.state.active_workflows,
        db_path=db_path,
        source="web",
        app_state=request.app.state,
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


async def _validate_quick_action(
    body: dict[str, Any], db_path: Path | None
) -> JSONResponse | tuple[str, str]:
    """Validate quick action request fields.

    Args:
        body: The parsed request body dict.
        db_path: Path to the SQLite database.

    Returns:
        A JSONResponse on validation error, or a ``(action, target_name)``
        tuple on success.
    """
    action = _str_field(body, "action")
    if action not in _QUICK_ACTIONS:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown action: {action}"},
        )

    if action in _QUICK_ACTION_PROVIDER_REQUIRED:
        provider_error = await _call_provider_validator(body, db_path)
        if provider_error is not None:
            return provider_error

    target_name = _str_field(body, "target_name")
    if not target_name:
        return JSONResponse(
            status_code=422,
            content={"detail": "target_name is required"},
        )

    try:
        validate_transport_and_command(body)
    except WorkflowValidationError as exc:
        return _validation_error_response(exc)

    if action == "campaign":
        try:
            validate_campaign_fields(body)
        except WorkflowValidationError as exc:
            return _validation_error_response(exc)

    return action, target_name


def _str_field(body: dict[str, Any], key: str, default: str = "") -> str:
    """Extract a string field from a request body, rejecting non-string values.

    Mirrors :func:`q_ai.services.workflow_service._str_field` — kept local
    for the action/target_name checks done above service dispatch.

    Args:
        body: The parsed request body dict.
        key: The field name to extract.
        default: Default value if key is missing or None.

    Returns:
        The stripped string value.

    Raises:
        TypeError: If the value is present but not a string.
    """
    val = body.get(key, default)
    if val is None:
        return default
    if not isinstance(val, str):
        raise TypeError(f"'{key}' must be a string")
    return val.strip()


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

    target_id = await asyncio.to_thread(_lookup_or_create_server_target, db_path, target_name)

    try:
        config = build_quick_action_config(action, body, target_id)
    except WorkflowValidationError as exc:
        return _validation_error_response(exc)

    runner = WorkflowRunner(
        workflow_id=_QUICK_ACTION_WORKFLOW_MAP[action],
        config=config,
        ws_manager=request.app.state.ws_manager,
        active_workflows=request.app.state.active_workflows,
        db_path=db_path,
        source="web",
        app_state=request.app.state,
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
