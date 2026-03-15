"""FastAPI callback server to capture agent hits.

This module implements the HTTP callback listener that receives
out-of-band requests from AI agents that have processed and executed
hidden payloads in canary documents. Incoming hits are recorded to
a SQLite database and logged to the console in real time.

Supports both authenticated (/c/{uuid}/{token}) and unauthenticated
(/c/{uuid}) callback URLs. Authenticated callbacks validate the
per-campaign token and receive high confidence scores. Unauthenticated
callbacks are still recorded but scored based on User-Agent analysis.

The server returns a fake 404 response on callback endpoints to avoid
alerting the target system that the payload was successfully executed.

Usage:
    From the CLI (preferred):

    >>> qai ipi listen --port 8080

    Programmatic:

    >>> from q_ai.ipi.server import start_server
    >>> start_server(host="127.0.0.1", port=8080)
"""

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from rich.console import Console
from rich.markup import escape

from q_ai.ipi import db
from q_ai.ipi.listener import record_hit, score_confidence
from q_ai.ipi.models import Hit, HitConfidence

from .api import api_router
from .ui import ui_router

console = Console()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle.

    Yields control to the application. Cleanup tasks run after yield
    on shutdown.

    Args:
        app: FastAPI application instance.

    Yields:
        None: Control is passed to the running application.
    """
    console.print("[green][OK][/green] Database initialized")
    yield


app = FastAPI(
    title="q-ai IPI Listener",
    description="Callback server for Indirect Prompt Injection detection",
    lifespan=lifespan,
)

# Mount static files and include web UI / API routers
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.include_router(api_router, prefix="/api")
app.include_router(ui_router, prefix="/ui")


_CONFIDENCE_STYLES = {
    HitConfidence.HIGH: "bold green",
    HitConfidence.MEDIUM: "bold yellow",
    HitConfidence.LOW: "bold red",
}
"""Rich markup styles for confidence level display."""


def log_hit_to_console(hit: Hit) -> None:
    """Print hit details to console with Rich formatting.

    Displays a prominent visual banner with the hit UUID, source IP,
    user-agent string, confidence level, token validation status,
    and any captured exfil data.

    Args:
        hit: Hit object containing callback metadata.
    """
    conf_style = _CONFIDENCE_STYLES.get(hit.confidence, "dim")
    token_indicator = "[green]+ valid[/green]" if hit.token_valid else "[red]x missing[/red]"

    console.print()
    console.print("=" * 60, style="bold yellow")
    console.print(f"[bold red]>>> HIT RECEIVED[/bold red] at {hit.timestamp.strftime('%H:%M:%S')}")
    console.print(f"   [bold]UUID:[/bold]       {escape(hit.uuid)}")
    console.print(f"   [bold]Token:[/bold]      {token_indicator}")
    console.print(
        f"   [bold]Confidence:[/bold] [{conf_style}]{hit.confidence.value}[/{conf_style}]"
    )
    console.print(f"   [bold]IP:[/bold]         {escape(hit.source_ip)}")
    console.print(f"   [bold]UA:[/bold]         {escape(hit.user_agent[:60])}...")
    if hit.body:
        console.print(f"   [bold yellow]DATA:[/bold yellow]       {escape(hit.body[:200])}")
        if len(hit.body) > 200:
            console.print(f"               [dim]({len(hit.body)} bytes total)[/dim]")
    console.print("=" * 60, style="bold yellow")
    console.print()


def _record_and_log_hit(hit: Hit) -> None:
    """Persist a hit to the database and log it to the console.

    Called as a background task from the callback endpoint so the
    HTTP response is returned immediately without blocking on I/O.

    Args:
        hit: Hit object to save and display.
    """
    record_hit(hit)
    log_hit_to_console(hit)


# =========================================================================
# Authenticated callback routes (/c/{uuid}/{token})
# These must be defined BEFORE the unauthenticated /c/{uuid} routes
# so FastAPI matches the more specific path first.
# =========================================================================


@app.get("/c/{canary_uuid}/{token}")
async def callback_authenticated(
    canary_uuid: str,
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> PlainTextResponse:
    """Receive and record an authenticated canary callback (GET).

    Validates the per-campaign token against the database. If the token
    matches, the hit is recorded with high confidence. If the UUID exists
    but the token is wrong, the hit is still recorded but with reduced
    confidence.

    Args:
        canary_uuid: UUID path parameter identifying the canary campaign.
        token: Per-campaign authentication token.
        request: Incoming FastAPI request object.
        background_tasks: FastAPI background task queue.

    Returns:
        PlainTextResponse with a spoofed 404 status code and body.
    """
    query_string = str(request.query_params) if request.query_params else None
    user_agent = request.headers.get("user-agent", "unknown")

    # Validate token against database
    campaign = db.get_campaign_by_token(canary_uuid, token)
    token_valid = campaign is not None
    confidence = score_confidence(token_valid, user_agent)

    hit = Hit(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        source_ip=request.client.host if request.client else "unknown",
        user_agent=user_agent,
        headers=json.dumps(dict(request.headers)),
        body=query_string,
        token_valid=token_valid,
        confidence=confidence,
        timestamp=datetime.now(UTC),
    )

    background_tasks.add_task(_record_and_log_hit, hit)

    return PlainTextResponse(
        "404 Not Found: The requested resource could not be located.",
        status_code=404,
    )


@app.post("/c/{canary_uuid}/{token}")
async def callback_authenticated_post(
    canary_uuid: str,
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> PlainTextResponse:
    """Receive and record an authenticated canary callback (POST).

    Validates the per-campaign token and captures POST body data
    for exfil payload types.

    Args:
        canary_uuid: UUID path parameter identifying the canary campaign.
        token: Per-campaign authentication token.
        request: Incoming FastAPI request object.
        background_tasks: FastAPI background task queue.

    Returns:
        PlainTextResponse with a spoofed 404 status code and body.
    """
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else None
    user_agent = request.headers.get("user-agent", "unknown")

    campaign = db.get_campaign_by_token(canary_uuid, token)
    token_valid = campaign is not None
    confidence = score_confidence(token_valid, user_agent)

    hit = Hit(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        source_ip=request.client.host if request.client else "unknown",
        user_agent=user_agent,
        headers=json.dumps(dict(request.headers)),
        body=body_text,
        token_valid=token_valid,
        confidence=confidence,
        timestamp=datetime.now(UTC),
    )

    background_tasks.add_task(_record_and_log_hit, hit)

    return PlainTextResponse(
        "404 Not Found: The requested resource could not be located.",
        status_code=404,
    )


# =========================================================================
# Unauthenticated callback routes (/c/{uuid})
# These still accept callbacks but mark them as token_valid=False.
# Confidence is scored based on User-Agent analysis only.
# =========================================================================


@app.get("/c/{canary_uuid}")
async def callback(
    canary_uuid: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> PlainTextResponse:
    """Receive and record an unauthenticated canary callback (GET).

    Records the callback with token_valid=False. Confidence is scored
    based on User-Agent analysis (medium for programmatic clients,
    low for browsers/scanners).

    Args:
        canary_uuid: UUID path parameter identifying the canary campaign.
        request: Incoming FastAPI request object.
        background_tasks: FastAPI background task queue.

    Returns:
        PlainTextResponse with a spoofed 404 status code and body.
    """
    query_string = str(request.query_params) if request.query_params else None
    user_agent = request.headers.get("user-agent", "unknown")
    confidence = score_confidence(False, user_agent)

    hit = Hit(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        source_ip=request.client.host if request.client else "unknown",
        user_agent=user_agent,
        headers=json.dumps(dict(request.headers)),
        body=query_string,
        token_valid=False,
        confidence=confidence,
        timestamp=datetime.now(UTC),
    )

    background_tasks.add_task(_record_and_log_hit, hit)

    return PlainTextResponse(
        "404 Not Found: The requested resource could not be located.",
        status_code=404,
    )


@app.post("/c/{canary_uuid}")
async def callback_post(
    canary_uuid: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> PlainTextResponse:
    """Receive and record an unauthenticated canary callback (POST).

    Records the callback with token_valid=False. Confidence is scored
    based on User-Agent analysis only.

    Args:
        canary_uuid: UUID path parameter identifying the canary campaign.
        request: Incoming FastAPI request object.
        background_tasks: FastAPI background task queue.

    Returns:
        PlainTextResponse with a spoofed 404 status code and body.
    """
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else None
    user_agent = request.headers.get("user-agent", "unknown")
    confidence = score_confidence(False, user_agent)

    hit = Hit(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        source_ip=request.client.host if request.client else "unknown",
        user_agent=user_agent,
        headers=json.dumps(dict(request.headers)),
        body=body_text,
        token_valid=False,
        confidence=confidence,
        timestamp=datetime.now(UTC),
    )

    background_tasks.add_task(_record_and_log_hit, hit)

    return PlainTextResponse(
        "404 Not Found: The requested resource could not be located.",
        status_code=404,
    )


@app.get("/health")
async def health() -> dict:
    """Return server health status.

    Provides a simple liveness check for monitoring and automated
    testing. Does not verify database connectivity.

    Returns:
        Dictionary with ``{"status": "ok"}``.
    """
    return {"status": "ok"}


def start_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start the callback listener server.

    Launches the uvicorn ASGI server bound to the specified host
    and port. The server runs in the foreground until interrupted
    with Ctrl+C.

    Args:
        host: Network interface to bind (default ``"127.0.0.1"``).
        port: TCP port to listen on (default ``8080``).
    """
    console.print(f"[bold green]Starting q-ai IPI listener on {host}:{port}[/bold green]")
    console.print(f"   Callback URL: [blue]http://<your-ip>:{port}/c/<uuid>/<token>[/blue]")
    console.print(f"   Dashboard:    [blue]http://localhost:{port}/ui/[/blue]")
    console.print("   Press [bold]Ctrl+C[/bold] to stop\n")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",
    )
