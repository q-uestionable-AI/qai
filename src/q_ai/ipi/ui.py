"""HTML page routes for the IPI web dashboard.

Serves full-page HTML templates using Jinja2. Each route renders
a complete page that extends the shared layout template. HTMX
handles dynamic updates by calling API endpoints for partial content.

All routes are mounted under ``/ui/`` by the main server module.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from q_ai.ipi import db
from q_ai.ipi.models import Format, PayloadStyle, PayloadType

from .generators import get_techniques_for_format

ui_router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@ui_router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the dashboard overview page.

    Args:
        request: FastAPI request.

    Returns:
        Rendered dashboard HTML page.
    """
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "active": "dashboard"},
    )


@ui_router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_list(request: Request) -> HTMLResponse:
    """Render the campaigns list page.

    Args:
        request: FastAPI request.

    Returns:
        Rendered campaigns HTML page with all campaigns and hit counts.
    """
    campaigns = db.get_all_campaigns()
    hits = db.get_hits()

    # Build hit count per campaign UUID
    hit_counts: dict[str, int] = {}
    for h in hits:
        hit_counts[h.uuid] = hit_counts.get(h.uuid, 0) + 1

    return templates.TemplateResponse(
        "campaigns.html",
        {
            "request": request,
            "active": "campaigns",
            "campaigns": campaigns,
            "hit_counts": hit_counts,
        },
    )


@ui_router.get("/campaigns/{uuid}", response_class=HTMLResponse)
async def campaign_detail(request: Request, uuid: str) -> Response:
    """Render a single campaign detail page.

    Args:
        request: FastAPI request.
        uuid: Campaign UUID.

    Returns:
        Rendered campaign detail HTML page, or redirect if not found.
    """
    campaign = db.get_campaign(uuid)
    if not campaign:
        return RedirectResponse(url="/ui/campaigns", status_code=302)
    hits = db.get_hits(uuid=uuid)
    return templates.TemplateResponse(
        "campaign_detail.html",
        {
            "request": request,
            "active": "campaigns",
            "campaign": campaign,
            "hits": hits,
        },
    )


@ui_router.get("/hits", response_class=HTMLResponse)
async def hits_page(request: Request) -> HTMLResponse:
    """Render the live hit feed page.

    Args:
        request: FastAPI request.

    Returns:
        Rendered hits HTML page.
    """
    return templates.TemplateResponse(
        "hits.html",
        {"request": request, "active": "hits"},
    )


@ui_router.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request) -> HTMLResponse:
    """Render the payload generation form page.

    Pre-populates format, technique, style, and payload type
    dropdowns from the model enums.

    Args:
        request: FastAPI request.

    Returns:
        Rendered generate HTML page.
    """
    default_format = Format.PDF
    techs = get_techniques_for_format(default_format)
    technique_names = [t.value for t in techs]

    return templates.TemplateResponse(
        "generate.html",
        {
            "request": request,
            "active": "generate",
            "formats": [f.value for f in Format],
            "techniques": technique_names,
            "styles": [s.value for s in PayloadStyle],
            "payload_types": [pt.value for pt in PayloadType],
        },
    )
