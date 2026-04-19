"""Intel page — import, probe."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile as _tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from q_ai.core.db import get_connection, get_target
from q_ai.server.routes._shared import (
    _get_db_path,
    _get_templates,
    logger,
)
from q_ai.services.run_service import (
    TargetOverviewRow,
    TargetsOverviewResult,
    query_target_overview_by_id,
    query_targets_overview,
)

router = APIRouter()

_background_tasks: set[asyncio.Task[None]] = set()


@router.get("/intel")
async def intel_page(request: Request) -> HTMLResponse:
    """Render the Intel landing page — target list with evidence summary."""
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
        {"active": "intel", "overview": overview, "targets": targets},
    )


@router.get("/intel/targets/{target_id}")
async def intel_target_detail(request: Request, target_id: str) -> HTMLResponse:
    """Render the per-target Intel detail page.

    Returns HTTP 404 with a plain HTML body when the target does not exist,
    matching the ``runs_compare`` HTML-404 convention.
    """
    templates = _get_templates(request)
    db_path = _get_db_path(request)

    def _load_row() -> TargetOverviewRow | None:
        with get_connection(db_path) as conn:
            return query_target_overview_by_id(conn, target_id)

    row = await asyncio.to_thread(_load_row)
    if row is None:
        return HTMLResponse(status_code=404, content="Target not found")

    return templates.TemplateResponse(
        request,
        "intel_target_detail.html",
        {"active": "intel", "overview_row": row},
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

    Expects multipart form with ``file``, ``format``, and optional
    ``target_id`` fields.  Returns the finding count and run ID.
    """
    from q_ai.imports.cli import _PARSERS, _persist

    form = await request.form()
    fmt = str(form.get("format") or "").strip()
    target_id = str(form.get("target_id") or "").strip() or None

    parser = _PARSERS.get(fmt)
    if parser is None:
        return JSONResponse(
            status_code=422,
            content={"detail": f"Unknown format '{fmt}'. Supported: {', '.join(sorted(_PARSERS))}"},
        )

    db_path = _get_db_path(request)

    # Validate target_id exists before persisting.
    if target_id:

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

    endpoint = (body.get("endpoint") or "").strip()
    model = (body.get("model") or "").strip()

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
        if not endpoint
        else "model is required"
        if not model
        else "concurrency must be >= 1"
        if concurrency < 1
        else None
    )
    if error:
        return JSONResponse(status_code=422, content={"detail": error})

    return {
        "endpoint": endpoint,
        "model": model,
        "api_key": (body.get("api_key") or "").strip() or None,
        "target_id": (body.get("target_id") or "").strip() or None,
        "temperature": temperature,
        "concurrency": concurrency,
    }


@router.post("/api/intel/probe/launch")
async def intel_probe_launch(request: Request) -> JSONResponse:
    """Launch IPI probing against a model endpoint.

    Runs probes in the background (fire-and-forget) and redirects
    to the runs page, following the same async pattern as workflow
    launches.
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
    api_key = validated["api_key"]
    target_id = validated["target_id"]
    temperature = validated["temperature"]
    concurrency = validated["concurrency"]

    try:
        probes = await asyncio.to_thread(load_probes)
    except (FileNotFoundError, ValueError):
        logger.exception("Failed to load probe definitions")
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to load probe definitions"},
        )

    db_path = _get_db_path(request)

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
            run_result = await run_probes(**probe_kwargs)
            await asyncio.to_thread(
                persist_probe_run,
                run_result,
                model=model,
                endpoint=endpoint,
                target_id=target_id,
                db_path=db_path,
            )
        except Exception:
            logger.error(
                "IPI probe background task failed",
            )

    task = asyncio.create_task(_run_probe_task())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        status_code=202,
        content={
            "status": "launched",
            "redirect": "/runs",
        },
    )
