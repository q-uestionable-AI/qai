"""Intel page — import, probe."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile as _tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from q_ai.core.db import create_target, get_connection, get_target, now_iso
from q_ai.ipi.sweep_selection import SelectionResult, select_template_for_target
from q_ai.server.routes._shared import (
    _get_db_path,
    _get_templates,
    logger,
)
from q_ai.services.run_service import (
    ImportRunSummary,
    ProbeRunSummary,
    SweepRunSummary,
    TargetOverviewRow,
    TargetsOverviewResult,
    query_target_import_runs,
    query_target_overview_by_id,
    query_target_probe_runs,
    query_target_sweep_runs,
    query_targets_overview,
)

router = APIRouter()

_background_tasks: set[asyncio.Task[None]] = set()


@router.get("/intel")
async def intel_page(request: Request) -> HTMLResponse:
    """Render the Intel landing page — target list with evidence summary."""
    from q_ai.ipi.models import CitationFrame, DocumentTemplate, PayloadStyle

    templates = _get_templates(request)
    db_path = _get_db_path(request)

    def _load_overview() -> TargetsOverviewResult:
        with get_connection(db_path) as conn:
            return query_targets_overview(conn)

    overview = await asyncio.to_thread(_load_overview)
    targets = [row.target for row in overview.rows]

    return templates.TemplateResponse(
        request,
        "intel.html",
        {
            "active": "intel",
            "overview": overview,
            "targets": targets,
            "sweep_templates": [t.value for t in DocumentTemplate],
            "sweep_styles": [s.value for s in PayloadStyle],
            "sweep_citation_frames": [f.value for f in CitationFrame],
            "sweep_citation_frame_default": CitationFrame.TEMPLATE_AWARE.value,
        },
    )


@router.get("/intel/targets/{target_id}")
async def intel_target_detail(request: Request, target_id: str) -> HTMLResponse:
    """Render the per-target Intel detail page.

    Returns HTTP 404 with a plain HTML body when the target does not exist,
    matching the ``runs_compare`` HTML-404 convention.
    """
    templates = _get_templates(request)
    db_path = _get_db_path(request)

    def _load_detail() -> tuple[
        TargetOverviewRow | None,
        list[SweepRunSummary],
        list[ProbeRunSummary],
        list[ImportRunSummary],
        SelectionResult | None,
    ]:
        with get_connection(db_path) as conn:
            row = query_target_overview_by_id(conn, target_id)
            if row is None:
                return None, [], [], [], None
            sweep_runs = query_target_sweep_runs(conn, target_id)
            probe_runs = query_target_probe_runs(conn, target_id)
            import_runs = query_target_import_runs(conn, target_id)
        # Evaluated on every GET. Per RFC-Intel-Target-Centric-Workspace-Design
        # Decision 5 Semantic Note, no caching — a stale button is worse than
        # re-running the selector (the helper opens its own connection).
        selection = select_template_for_target(target_id, db_path=db_path)
        return row, sweep_runs, probe_runs, import_runs, selection

    row, sweep_runs, probe_runs, import_runs, selection = await asyncio.to_thread(_load_detail)
    if row is None:
        return HTMLResponse(status_code=404, content="Target not found")

    return templates.TemplateResponse(
        request,
        "intel_target_detail.html",
        {
            "active": "intel",
            "overview_row": row,
            "sweep_runs": sweep_runs,
            "probe_runs": probe_runs,
            "import_runs": import_runs,
            "selection": selection,
            "selection_kind": type(selection).__name__ if selection is not None else None,
        },
    )


@router.post("/api/intel/import/preview")
async def intel_import_preview(
    request: Request,
    file: UploadFile,
) -> JSONResponse:
    """Parse an uploaded file and return a preview without writing to DB.

    Expects multipart form with ``file`` and ``format`` fields.
    Returns finding summaries for display before committing.
    """
    from q_ai.imports.cli import _PARSERS

    form = await request.form()
    fmt = str(form.get("format") or "").strip()

    parser = _PARSERS.get(fmt)
    if parser is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown format '{fmt}'. Supported: {', '.join(sorted(_PARSERS))}"},
        )

    suffix = Path(file.filename or "upload").suffix or ".tmp"
    file_bytes = await file.read()

    from q_ai.imports.models import ImportResult

    def _parse_file() -> ImportResult:
        tmp_path: Path | None = None
        try:
            with _tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(file_bytes)
            return parser(tmp_path)
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

    try:
        result = await asyncio.to_thread(_parse_file)
    except (ValueError, TypeError, OSError):
        logger.exception("Import preview failed for format=%s", fmt)
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Failed to parse the uploaded file. Check format and file contents.",
            },
        )

    findings = [
        {
            "severity": f.severity.name,
            "category": f.category,
            "title": f.title,
        }
        for f in result.findings
    ]

    return JSONResponse(
        content={
            "finding_count": len(result.findings),
            "warning_count": len(result.errors),
            "findings": findings,
        }
    )


@router.post("/api/intel/import/commit")
async def intel_import_commit(
    request: Request,
    file: UploadFile,
) -> JSONResponse:
    """Parse an uploaded file and persist findings to the database.

    Expects multipart form with ``file``, ``format``, and ``target_id``
    fields. ``target_id`` is required (Phase 5 — required target binding
    per RFC Design Decision 3). Returns the finding count and run ID.
    """
    from q_ai.imports.cli import _PARSERS, _persist

    form = await request.form()
    fmt = str(form.get("format") or "").strip()
    target_id = str(form.get("target_id") or "").strip()

    if not target_id:
        return JSONResponse(
            status_code=422,
            content={"detail": "target_id is required"},
        )

    parser = _PARSERS.get(fmt)
    if parser is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown format '{fmt}'. Supported: {', '.join(sorted(_PARSERS))}"},
        )

    db_path = _get_db_path(request)

    def _check_target() -> bool:
        with get_connection(db_path) as conn:
            return get_target(conn, target_id) is not None

    if not await asyncio.to_thread(_check_target):
        return JSONResponse(
            status_code=422,
            content={"detail": "Target not found"},
        )

    suffix = Path(file.filename or "upload").suffix or ".tmp"
    file_bytes = await file.read()

    from q_ai.imports.models import ImportResult

    def _parse_and_persist() -> tuple[ImportResult, str]:
        tmp_path: Path | None = None
        try:
            with _tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(file_bytes)
            result = parser(tmp_path)
            run_id = _persist(result, db_path, tmp_path, target_id=target_id)
            return result, run_id
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

    try:
        result, run_id = await asyncio.to_thread(_parse_and_persist)
    except (ValueError, TypeError, OSError):
        logger.exception("Import commit failed for format=%s", fmt)
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Failed to import the uploaded file. Check format and file contents.",
            },
        )

    return JSONResponse(
        status_code=201,
        content={
            "finding_count": len(result.findings),
            "warning_count": len(result.errors),
            "run_id": run_id,
        },
    )


def _coerce_optional_string(
    raw: Any,
    field: str,
) -> str | JSONResponse:
    """Coerce a JSON value to a stripped string, or return a 422 response.

    Accepts missing / null / empty values as the empty string. Non-string
    JSON types (int, list, dict, bool) return a 422 rather than crashing
    with ``AttributeError`` on ``.strip()``.

    Args:
        raw: Value pulled from the JSON body.
        field: Field name for the error detail.

    Returns:
        The stripped string on success, or a ``JSONResponse`` on error.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return JSONResponse(
            status_code=422,
            content={"detail": f"{field} must be a string"},
        )
    return raw.strip()


def _read_api_key_header(request: Request) -> str | None:
    """Read the api_key from the ``X-API-Key`` request header.

    Used by the probe and sweep launch handlers to keep the upstream-
    provider credential out of the JSON body (and out of any logs that
    dump ``request.json()``). Absent, empty, or whitespace-only header
    values all map to ``None`` — the downstream kwargs treat that as
    "no auth", matching the body-field semantics these endpoints had
    before Phase 6.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The stripped header value, or ``None`` when the header is
        missing or blank.
    """
    raw = request.headers.get("X-API-Key")
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _validate_create_target_body(
    body: Any,
) -> dict[str, Any] | JSONResponse:
    """Validate a ``/api/intel/targets/create`` request body.

    Mirrors :func:`_validate_probe_body` in shape: per-field coercion via
    :func:`_coerce_optional_string`, with 422 JSON responses on failure.
    Required fields are ``name`` and ``type`` (both non-empty after strip).
    Optional fields are ``uri`` (nullable string) and ``metadata`` (nullable
    dict of string → string).

    Args:
        body: Parsed JSON body (may be any type).

    Returns:
        Dict with validated ``name``/``type``/``uri``/``metadata`` keys on
        success (``uri`` and ``metadata`` are ``None`` when absent), or a
        ``JSONResponse`` 422 on failure.
    """
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request body must be a JSON object"},
        )

    strings: dict[str, str] = {}
    for field in ("name", "type", "uri"):
        coerced = _coerce_optional_string(body.get(field), field)
        if isinstance(coerced, JSONResponse):
            return coerced
        strings[field] = coerced

    error = (
        "name is required"
        if not strings["name"]
        else "type is required"
        if not strings["type"]
        else None
    )
    if error:
        return JSONResponse(status_code=422, content={"detail": error})

    metadata_raw = body.get("metadata")
    metadata: dict[str, str] | None
    if metadata_raw is None:
        metadata = None
    elif not isinstance(metadata_raw, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "metadata must be an object"},
        )
    else:
        for k, v in metadata_raw.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return JSONResponse(
                    status_code=422,
                    content={"detail": "metadata keys and values must be strings"},
                )
        metadata = dict(metadata_raw)

    return {
        "name": strings["name"],
        "type": strings["type"],
        "uri": strings["uri"] or None,
        "metadata": metadata,
    }


@router.post("/api/intel/targets/create")
async def intel_create_target(request: Request) -> JSONResponse:
    """Create a new target from the Intel page inline-creation modal.

    Thin wrapper over :func:`q_ai.core.db.create_target`. Blocking DB work
    runs inside ``asyncio.to_thread`` to keep the event loop free.

    Args:
        request: Incoming HTTP request with a JSON body.

    Returns:
        JSONResponse with status 201 and body ``{"target_id", "name",
        "type"}`` on success (``uri`` and ``metadata`` included when
        supplied). 400 on malformed JSON, 422 on validation failure.

    Notes:
        Name collisions still return 201 (per PD #2 in the Phase 5 brief —
        the client gets a non-blocking warning from
        ``/api/targets/check-name`` before submit).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    validated = _validate_create_target_body(body)
    if isinstance(validated, JSONResponse):
        return validated

    name = validated["name"]
    type_ = validated["type"]
    uri = validated["uri"]
    metadata = validated["metadata"]

    db_path = _get_db_path(request)

    def _create_sync() -> str:
        with get_connection(db_path) as conn:
            return create_target(conn, type=type_, name=name, uri=uri, metadata=metadata)

    target_id = await asyncio.to_thread(_create_sync)

    payload: dict[str, Any] = {
        "target_id": target_id,
        "name": name,
        "type": type_,
    }
    if uri is not None:
        payload["uri"] = uri
    if metadata is not None:
        payload["metadata"] = metadata
    return JSONResponse(status_code=201, content=payload)


def _validate_probe_body(
    body: Any,
) -> dict[str, Any] | JSONResponse:
    """Validate and extract probe launch parameters from a JSON body.

    Args:
        body: Parsed JSON body (may be any type).

    Returns:
        A dict of validated parameters, or a JSONResponse on error.
    """
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request body must be a JSON object"},
        )

    strings: dict[str, str] = {}
    for field in ("endpoint", "model", "target_id"):
        coerced = _coerce_optional_string(body.get(field), field)
        if isinstance(coerced, JSONResponse):
            return coerced
        strings[field] = coerced

    try:
        temperature = float(body.get("temperature", 0.0))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "temperature must be a number"},
        )

    try:
        concurrency = int(body.get("concurrency", 1))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"detail": "concurrency must be an integer"},
        )

    # Check required fields and value constraints together.
    error = (
        "endpoint is required"
        if not strings["endpoint"]
        else "model is required"
        if not strings["model"]
        else "concurrency must be >= 1"
        if concurrency < 1
        else None
    )
    if error:
        return JSONResponse(status_code=422, content={"detail": error})

    # api_key is sourced from the ``X-API-Key`` header, not this body.
    # Any ``api_key`` field present in the body is silently ignored.
    return {
        "endpoint": strings["endpoint"],
        "model": strings["model"],
        "target_id": strings["target_id"] or None,
        "temperature": temperature,
        "concurrency": concurrency,
    }


@router.post("/api/intel/probe/launch")
async def intel_probe_launch(request: Request) -> JSONResponse:
    """Launch IPI probing against a model endpoint.

    Runs probes in the background (fire-and-forget) and returns 202
    with a redirect URL. The run row is created inside
    :func:`persist_probe_run` *after* the probe HTTP calls complete,
    which is after this response is returned — so the response body
    intentionally does not include a ``run_id`` and the redirect
    targets the Probe Runs *section*, not a specific row. Callers see
    the new run on the next page load of the target detail page
    (when ``target_id`` is set) or the Intel landing page.

    Response shape: ``{"status": "launched", "redirect": "<url>"}``.
    Redirect is ``/intel/targets/<target_id>#probe-runs`` when a
    target is supplied, else ``/intel``. Mirrors
    :func:`intel_sweep_launch`.
    """
    from q_ai.ipi.probe_service import load_probes, persist_probe_run, run_probes

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    validated = _validate_probe_body(body)
    if isinstance(validated, JSONResponse):
        return validated

    endpoint = validated["endpoint"]
    model = validated["model"]
    target_id = validated["target_id"]
    temperature = validated["temperature"]
    concurrency = validated["concurrency"]
    api_key = _read_api_key_header(request)

    db_path = _get_db_path(request)

    # Validate target existence off the event loop before loading probe
    # definitions or scheduling the background task — mirrors
    # :func:`intel_sweep_launch`. Without this check, a nonexistent
    # target_id accepts a 202 and a redirect to
    # ``/intel/targets/<bogus>#probe-runs``, which 404s; the background
    # task then fails silently on the target_id FK constraint.
    if target_id:

        def _check_target() -> bool:
            with get_connection(db_path) as conn:
                return get_target(conn, target_id) is not None

        if not await asyncio.to_thread(_check_target):
            return JSONResponse(
                status_code=422,
                content={"detail": "Target not found"},
            )

    try:
        probes = await asyncio.to_thread(load_probes)
    except (FileNotFoundError, ValueError):
        logger.exception("Failed to load probe definitions")
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to load probe definitions"},
        )

    # Bundle run_probes kwargs here so api_key stays out of the
    # closure's own scope — prevents CodeQL data-flow from the
    # secret into the except block's frame locals.
    probe_kwargs: dict[str, Any] = {
        "endpoint": endpoint,
        "model": model,
        "probes": probes,
        "api_key": api_key,
        "temperature": temperature,
        "concurrency": concurrency,
    }

    async def _run_probe_task() -> None:
        try:
            started_at = now_iso()
            run_result = await run_probes(**probe_kwargs)
            await asyncio.to_thread(
                persist_probe_run,
                run_result,
                model=model,
                endpoint=endpoint,
                target_id=target_id,
                db_path=db_path,
                started_at=started_at,
            )
        except Exception:
            logger.error(
                "IPI probe background task failed",
            )

    task = asyncio.create_task(_run_probe_task())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    redirect = f"/intel/targets/{target_id}#probe-runs" if target_id else "/intel"
    return JSONResponse(
        status_code=202,
        content={
            "status": "launched",
            "redirect": redirect,
        },
    )


def _coerce_enum_list(
    raw: Any,
    enum_cls: type,
    missing_detail: str,
    unknown_template: str,
) -> list[Any] | JSONResponse:
    """Coerce a JSON list into enum members, or return a 422 response.

    Iterates per-item so the 422 detail can quote the raw offending value
    rather than the enum constructor's exception message — the latter
    would leak the class name and exposes a stack-trace-like string to
    the client (CodeQL ``py/stack-trace-exposure`` sink).

    Args:
        raw: The raw JSON value for the list field.
        enum_cls: The target enum class (e.g. ``DocumentTemplate``).
        missing_detail: Error detail when the list is missing/empty/non-list.
        unknown_template: ``str.format``-ready template with ``{value}``
            placeholder for the offending value.

    Returns:
        List of enum members on success, or ``JSONResponse`` on failure.
    """
    if not isinstance(raw, list) or not raw:
        return JSONResponse(status_code=422, content={"detail": missing_detail})
    members: list[Any] = []
    for v in raw:
        # TypeError covers non-hashable JSON values like dicts/lists;
        # ValueError/KeyError cover the normal "not a valid member" paths.
        try:
            members.append(enum_cls(v))
        except (TypeError, ValueError, KeyError):
            safe_value = v if isinstance(v, str) else repr(v)
            return JSONResponse(
                status_code=422,
                content={"detail": unknown_template.format(value=safe_value)},
            )
    return members


def _validate_sweep_strings(body: dict[str, Any]) -> dict[str, str] | JSONResponse:
    """Coerce the string-typed fields of a sweep body to stripped strings.

    Args:
        body: Parsed JSON body (already known to be a dict).

    Returns:
        Dict of field-name -> stripped string on success, or a
        ``JSONResponse`` 422 if any field is present with a non-string
        type.
    """
    fields = ("endpoint", "model", "target_id", "payload_type", "citation_frame")
    out: dict[str, str] = {}
    for field in fields:
        coerced = _coerce_optional_string(body.get(field), field)
        if isinstance(coerced, JSONResponse):
            return coerced
        out[field] = coerced
    return out


def _validate_sweep_scalars(body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Validate the scalar (non-list) fields of a sweep launch body.

    Type-hardens the string fields first (non-string JSON values return
    422 rather than crashing ``.strip()`` with ``AttributeError``), then
    coerces numerics and enforces value constraints.
    """
    strings = _validate_sweep_strings(body)
    if isinstance(strings, JSONResponse):
        return strings

    try:
        temperature = float(body.get("temperature", 0.0))
    except (TypeError, ValueError):
        return JSONResponse(status_code=422, content={"detail": "temperature must be a number"})

    try:
        concurrency = int(body.get("concurrency", 1))
    except (TypeError, ValueError):
        return JSONResponse(status_code=422, content={"detail": "concurrency must be an integer"})

    try:
        reps = int(body.get("reps", 3))
    except (TypeError, ValueError):
        return JSONResponse(status_code=422, content={"detail": "reps must be an integer"})

    payload_type_raw = strings["payload_type"] or "callback"
    from q_ai.ipi.models import CitationFrame

    citation_frame_raw = strings["citation_frame"] or CitationFrame.TEMPLATE_AWARE.value
    citation_frame_valid = {f.value for f in CitationFrame}

    error = (
        "endpoint is required"
        if not strings["endpoint"]
        else "model is required"
        if not strings["model"]
        else "concurrency must be >= 1"
        if concurrency < 1
        else "reps must be >= 1"
        if reps < 1
        else "payload_type must be 'callback' in v1"
        if payload_type_raw != "callback"
        else (
            f"citation_frame must be one of "
            f"{sorted(citation_frame_valid)} (got '{citation_frame_raw}')"
        )
        if citation_frame_raw not in citation_frame_valid
        else None
    )
    if error:
        return JSONResponse(status_code=422, content={"detail": error})

    # api_key is sourced from the ``X-API-Key`` header, not this body.
    # Any ``api_key`` field present in the body is silently ignored.
    return {
        "endpoint": strings["endpoint"],
        "model": strings["model"],
        "target_id": strings["target_id"] or None,
        "temperature": temperature,
        "concurrency": concurrency,
        "reps": reps,
        "citation_frame": CitationFrame(citation_frame_raw),
    }


def _validate_sweep_body(
    body: Any,
) -> dict[str, Any] | JSONResponse:
    """Validate and extract sweep launch parameters from a JSON body.

    Purely synchronous validation — no DB work. The caller is
    responsible for verifying ``target_id`` existence off the event
    loop (see :func:`intel_sweep_launch`), mirroring the pattern in
    :func:`intel_import_commit`.

    Args:
        body: Parsed JSON body (may be any type).

    Returns:
        A dict of validated parameters, or a JSONResponse on error.
    """
    from q_ai.ipi.models import DocumentTemplate, PayloadStyle

    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request body must be a JSON object"},
        )

    scalars = _validate_sweep_scalars(body)
    if isinstance(scalars, JSONResponse):
        return scalars

    templates_result = _coerce_enum_list(
        body.get("templates"),
        DocumentTemplate,
        missing_detail="at least one template is required",
        unknown_template="unknown template '{value}'",
    )
    if isinstance(templates_result, JSONResponse):
        return templates_result
    styles_result = _coerce_enum_list(
        body.get("styles"),
        PayloadStyle,
        missing_detail="at least one style is required",
        unknown_template="unknown style '{value}'",
    )
    if isinstance(styles_result, JSONResponse):
        return styles_result

    return {**scalars, "templates": templates_result, "styles": styles_result}


@router.post("/api/intel/sweep/launch")
async def intel_sweep_launch(request: Request) -> JSONResponse:
    """Launch an IPI sweep against a model endpoint.

    Runs the sweep in the background (fire-and-forget) and returns 202
    with a redirect URL. The run row is created inside
    :func:`persist_sweep_run` *after* the sweep HTTP calls complete,
    which is after this response is returned — so the response body
    intentionally does not include a ``run_id``. Callers see the run
    on the next page load of the target detail page (when ``target_id``
    is set) or the Runs page.

    Response shape: ``{"status": "launched", "redirect": "<url>"}``.
    Redirect is ``/intel/targets/<target_id>#sweep-runs`` when a target
    is supplied, else ``/intel``.
    """
    from q_ai.ipi.models import PayloadType
    from q_ai.ipi.sweep_service import build_sweep_cases, persist_sweep_run, run_sweep

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    validated = _validate_sweep_body(body)
    if isinstance(validated, JSONResponse):
        return validated

    endpoint = validated["endpoint"]
    model = validated["model"]
    target_id = validated["target_id"]
    db_path = _get_db_path(request)

    # Target existence check is off the event loop (sqlite is blocking),
    # mirroring intel_import_commit's pattern above.
    if target_id:

        def _check_target() -> bool:
            with get_connection(db_path) as conn:
                return get_target(conn, target_id) is not None

        if not await asyncio.to_thread(_check_target):
            return JSONResponse(
                status_code=422,
                content={"detail": "Target not found"},
            )

    cases = build_sweep_cases(
        validated["templates"],
        validated["styles"],
        PayloadType.CALLBACK,
    )

    # Bundle run_sweep kwargs here so api_key stays out of the closure's
    # own scope — prevents CodeQL data-flow from the secret into the
    # except block's frame locals. Same pattern as _run_probe_task.
    sweep_kwargs: dict[str, Any] = {
        "endpoint": endpoint,
        "model": model,
        "cases": cases,
        "reps": validated["reps"],
        "temperature": validated["temperature"],
        "concurrency": validated["concurrency"],
        "api_key": _read_api_key_header(request),
        "citation_frame": validated["citation_frame"],
    }

    async def _run_sweep_task() -> None:
        try:
            started_at = now_iso()
            run_result = await run_sweep(**sweep_kwargs)
            await asyncio.to_thread(
                persist_sweep_run,
                run_result,
                model=model,
                endpoint=endpoint,
                target_id=target_id,
                db_path=db_path,
                started_at=started_at,
            )
        except Exception:
            logger.error("IPI sweep background task failed")

    task = asyncio.create_task(_run_sweep_task())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    redirect = f"/intel/targets/{target_id}#sweep-runs" if target_id else "/intel"
    return JSONResponse(
        status_code=202,
        content={
            "status": "launched",
            "redirect": redirect,
        },
    )
