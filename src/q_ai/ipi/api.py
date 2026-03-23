"""JSON API endpoints for the IPI web dashboard.

Provides RESTful endpoints for retrieving campaigns, hits, statistics,
generating payloads, and dynamic technique lookups. These endpoints
serve HTMX partial templates for the web UI and can also be consumed
as a standalone JSON API.

All endpoints are mounted under ``/api/`` by the main server module.

Usage:
    The API router is included in the FastAPI app::

        from q_ai.ipi.api import api_router
        app.include_router(api_router, prefix="/api")
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from q_ai.ipi import db
from q_ai.ipi.models import Format, HitConfidence, PayloadStyle, PayloadType, Technique

from .generate_service import generate_documents
from .generators import get_techniques_for_format

api_router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Fixed output base for web API requests. Uses a user-home-based path
# so it is independent of the server's working directory. CLI users control
# their own output paths; only the web API needs sandboxing.
_API_OUTPUT_BASE = (Path.home() / ".qai" / "payloads").resolve()


def _validate_output_path(output: Path, base_dir: Path) -> Path:
    """Resolve and validate an output path against directory traversal.

    Ensures the resolved path stays within the trusted base directory.
    Used exclusively by the web API where output paths are not
    operator-controlled.

    Args:
        output: Untrusted output path from the web form.
        base_dir: Trusted root directory that ``output`` must stay within.

    Returns:
        Resolved absolute Path guaranteed to be under ``base_dir``.

    Raises:
        ValueError: If the resolved path escapes ``base_dir``.
    """
    resolved = (base_dir / output).resolve()
    try:
        resolved.relative_to(base_dir)
    except ValueError:
        raise ValueError(
            f"Output path escapes allowed base directory: {resolved} is not under {base_dir}"
        ) from None
    return resolved


@api_router.get("/stats", response_class=HTMLResponse)
async def get_stats(request: Request) -> HTMLResponse:
    """Return dashboard statistics as an HTMX partial.

    Aggregates campaign counts and hit confidence breakdowns for
    the dashboard overview cards.

    Args:
        request: FastAPI request (required for Jinja2 template context).

    Returns:
        Rendered HTML partial with stat cards.
    """
    campaigns = db.get_all_campaigns()
    hits = db.get_hits()

    high = sum(1 for h in hits if h.confidence == HitConfidence.HIGH)
    medium = sum(1 for h in hits if h.confidence == HitConfidence.MEDIUM)
    low = sum(1 for h in hits if h.confidence == HitConfidence.LOW)

    return templates.TemplateResponse(
        request,
        "partials/stats.html",
        {
            "total_campaigns": len(campaigns),
            "total_hits": len(hits),
            "high": high,
            "medium": medium,
            "low": low,
            "reset_message": None,
        },
    )


@api_router.get("/hits", response_class=HTMLResponse)
async def get_hits_partial(
    request: Request, limit: int = 50, uuid: str | None = None
) -> HTMLResponse:
    """Return hit list as an HTMX partial.

    Args:
        request: FastAPI request.
        limit: Maximum number of hits to return.
        uuid: Optional campaign UUID filter.

    Returns:
        Rendered HTML partial with hit cards.
    """
    hits = db.get_hits(uuid=uuid)[:limit]
    return templates.TemplateResponse(request, "partials/hit_list.html", {"hits": hits})


@api_router.get("/techniques", response_class=HTMLResponse)
async def get_technique_options(request: Request, format: str = "pdf") -> HTMLResponse:
    """Return technique <option> elements for a given format.

    Called by HTMX when the format dropdown changes on the
    generate page.

    Args:
        request: FastAPI request.
        format: Format name to get techniques for.

    Returns:
        Rendered HTML partial with technique select options.
    """
    try:
        fmt = Format(format)
        techs = get_techniques_for_format(fmt)
        technique_names = [t.value for t in techs]
    except ValueError:
        technique_names = []

    return templates.TemplateResponse(
        request, "partials/technique_options.html", {"techniques": technique_names}
    )


@api_router.post("/generate", response_class=HTMLResponse)
async def generate_payloads(
    request: Request,
    callback_url: str = Form(...),
    format: str = Form("pdf"),
    technique: str = Form("all"),
    payload_style: str = Form("citation"),
    payload_type: str = Form("callback"),
    base_name: str = Form("report"),
    seed: str = Form(""),
) -> HTMLResponse:
    """Generate payload documents from the web form.

    Output is written to a fixed ``~/.qai/payloads/generate/``
    directory. The path is validated against directory traversal
    and is not user-controllable.

    Args:
        request: FastAPI request.
        callback_url: Callback listener URL.
        format: Document format name.
        technique: Technique name or "all".
        payload_style: Social engineering style.
        payload_type: Attack objective type.
        base_name: Base filename prefix.
        seed: Optional seed for deterministic generation.

    Returns:
        Rendered HTML partial with generation results or errors.
    """
    # Sanitize base_name at input boundary (defense in depth —
    # generate_service also sanitizes, but strip traversal early).
    base_name = Path(base_name).name
    if not base_name or base_name in (".", ".."):
        return templates.TemplateResponse(
            request, "partials/gen_result.html", {"error": "Invalid base filename."}
        )

    # Fixed output directory — validated against traversal
    output_dir = _validate_output_path(Path("generate"), _API_OUTPUT_BASE)
    try:
        fmt = Format(format)
        style = PayloadStyle(payload_style)
        ptype = PayloadType(payload_type)
        seed_val = int(seed) if seed.strip() else None

        # Resolve techniques
        available = get_techniques_for_format(fmt)
        if technique == "all":
            techs = available
        else:
            tech_enum = Technique(technique)
            if tech_enum not in available:
                return templates.TemplateResponse(
                    request,
                    "partials/gen_result.html",
                    {"error": f"Technique '{technique}' not available for {format}"},
                )
            techs = [tech_enum]

        result = generate_documents(
            callback_url=callback_url,
            output=output_dir,
            format_name=fmt,
            techniques=techs,
            payload_style=style,
            payload_type=ptype,
            base_name=base_name,
            seed=seed_val,
        )

        return templates.TemplateResponse(request, "partials/gen_result.html", {"result": result})
    except ValueError as e:
        # ValueError from Format/PayloadStyle/PayloadType/Technique parsing —
        # safe to surface since these are user-input validation errors.
        return templates.TemplateResponse(request, "partials/gen_result.html", {"error": str(e)})
    except Exception:
        # Unexpected errors — log internally but don't leak details to client.
        logging.exception("Unexpected error during payload generation")
        return templates.TemplateResponse(
            request,
            "partials/gen_result.html",
            {"error": "An internal error occurred during generation."},
        )


@api_router.post("/reset", response_class=HTMLResponse)
async def reset_data(request: Request) -> HTMLResponse:
    """Reset all campaigns, hits, and generated files.

    Called by the dashboard reset button via HTMX. Deletes generated
    payload files from disk and clears the database. Returns a
    confirmation partial that replaces the stats panel.

    Args:
        request: FastAPI request.

    Returns:
        Rendered HTML partial confirming the reset.
    """
    campaigns_deleted, hits_deleted, files_deleted = db.reset_db()
    return templates.TemplateResponse(
        request,
        "partials/stats.html",
        {
            "total_campaigns": 0,
            "total_hits": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "reset_message": (
                f"Cleared {campaigns_deleted} campaigns, "
                f"{hits_deleted} hits, and {files_deleted} files."
            ),
        },
    )
