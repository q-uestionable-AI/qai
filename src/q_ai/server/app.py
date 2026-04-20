"""FastAPI application factory for the q-ai web UI."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"

_UNBOUND_TARGET_NAME = "(Unbound historical intel)"
_UNBOUND_METADATA_KIND = "synthetic-unbound"


def _migrate_unbound_runs(db_path: Path | None) -> None:
    """Reparent historical NULL-``target_id`` runs to a synthetic target.

    One-time idempotent migration run on every server start. Phase 5 of
    the Intel target-centric workspace work (RFC Design Decision 3 —
    required target binding at every ingestion point).

    Behavior:
      1. Existence check: look up a target with
         ``json_extract(metadata, '$.kind') = 'synthetic-unbound'``.
      2. If absent, create one: ``type='virtual'``, name
         ``"(Unbound historical intel)"``, metadata
         ``{"kind": "synthetic-unbound"}``.
      3. Config-aware backfill: parent workflow runs persist their
         target binding in ``config['target_id']`` (see
         :meth:`WorkflowRunner.start` — the runs row is created with
         ``target_id`` NULL and the real id is stashed in the config
         JSON blob). Before the catch-all below, promote those rows to
         use their config-bound target where the id still exists.
      4. Reparent: ``UPDATE runs SET target_id = ? WHERE target_id IS
         NULL`` — module-agnostic, all remaining historically unbound
         runs bucket into the synthetic Unbound target.

    Both UPDATEs run inside one transaction so the backfill-then-bucket
    split is atomic.

    Idempotent: on a second call the synthetic target already exists
    and zero NULL rows remain, so both steps are no-ops.

    Failure mode: callers wrap this in try/except. A failure must not
    prevent server startup — a future restart retries.

    Args:
        db_path: Path to the SQLite database, or ``None`` for default.
    """
    from q_ai.core.db import create_target, get_connection

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM targets WHERE json_extract(metadata, '$.kind') = ? LIMIT 1",
            (_UNBOUND_METADATA_KIND,),
        ).fetchone()

        if existing is None:
            target_id = create_target(
                conn,
                type="virtual",
                name=_UNBOUND_TARGET_NAME,
                metadata={"kind": _UNBOUND_METADATA_KIND},
            )
            created = True
        else:
            target_id = existing["id"]
            created = False

        # Step 1 — promote NULL-target_id runs whose config carries a
        # valid target_id (e.g. workflow parent runs). Only accept the
        # binding if the referenced target row still exists, so orphaned
        # references fall through to the Unbound bucket below.
        backfill_cursor = conn.execute(
            """
            UPDATE runs
               SET target_id = json_extract(config, '$.target_id')
             WHERE target_id IS NULL
               AND json_extract(config, '$.target_id') IS NOT NULL
               AND json_extract(config, '$.target_id') IN (SELECT id FROM targets)
            """
        )
        backfilled = backfill_cursor.rowcount

        # Step 2 — everything still unbound goes to the synthetic target.
        reparent_cursor = conn.execute(
            "UPDATE runs SET target_id = ? WHERE target_id IS NULL",
            (target_id,),
        )
        reparented = reparent_cursor.rowcount
        conn.commit()

    if created:
        logger.info(
            "Created synthetic Unbound target %s; "
            "restored %d config-bound run(s); reparented %d run(s)",
            target_id,
            backfilled,
            reparented,
        )
    else:
        logger.info(
            "Synthetic Unbound target already exists (%s); "
            "restored %d config-bound run(s); %d NULL-target_id run(s) found",
            target_id,
            backfilled,
            reparented,
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    Startup:
      1. Detects runs left in ``WAITING_FOR_USER`` by a previous server
         process. Those runs cannot be resumed because the in-memory
         ``WorkflowRunner`` is gone; this records them so route handlers
         can surface them to the operator for manual conclusion.
      2. Reattaches to a still-live tunneled listener, either registering
         it as an ``adopted`` managed listener (when ``manager == "web-ui"``)
         or recording it as a foreign listener (otherwise).
    Shutdown: reserved for cleanup.
    """
    import asyncio
    import datetime as _dt

    from q_ai.core.db import get_connection
    from q_ai.core.models import RunStatus
    from q_ai.services import run_service
    from q_ai.services.managed_listener import (
        MANAGER_CLI,
        detect_existing_listener,
        start_adopted_poller,
    )

    # Phase 5 — one-time idempotent migration that reparents historical
    # NULL-target_id runs to a synthetic Unbound target. Wrapped in
    # try/except so migration failure does not block server startup
    # (Risk #2 in the Phase 5 brief — existing users must still reach
    # the UI; a follow-up restart retries the migration).
    try:
        await asyncio.to_thread(_migrate_unbound_runs, app.state.db_path)
    except Exception:
        logger.exception("Unbound-runs migration failed; continuing startup")

    stranded: dict[str, tuple[str | None, _dt.datetime | None]] = {}
    with get_connection(app.state.db_path) as conn:
        waiting = run_service.list_runs(conn, status=RunStatus.WAITING_FOR_USER)
    for run in waiting:
        stranded[run.id] = (run.name, run.started_at)
    app.state.stranded_runs = stranded
    if stranded:
        logger.warning(
            "Detected %d stranded WAITING_FOR_USER run(s) from a previous server process: %s",
            len(stranded),
            ", ".join(stranded),
        )

    qai_dir = getattr(app.state, "qai_dir", None)
    adopted, foreign = detect_existing_listener(qai_dir=qai_dir)
    if adopted is not None:
        app.state.managed_listeners[adopted.listener_id] = adopted
        logger.warning(
            "Reattached managed listener from previous server process: "
            "listener_id=%s pid=%d url=%s",
            adopted.listener_id,
            adopted.pid,
            adopted.public_url,
        )
    if foreign is not None:
        app.state.foreign_listener = foreign
        logger.warning(
            "Detected foreign tunneled listener: pid=%d url=%s manager=%s",
            foreign.pid,
            foreign.public_url,
            foreign.manager or MANAGER_CLI,
        )
    # Start the adopted-listener liveness poller so handles whose PIDs
    # die externally (the server didn't spawn them, so there's no drain
    # thread to notice) transition to CRASHED within one polling cycle.
    app.state._adopted_poller_stop = start_adopted_poller(app.state.managed_listeners)
    try:
        yield
    finally:
        app.state._adopted_poller_stop.set()


def create_app(db_path: Path | None = None, qai_dir: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Optional database path override. Defaults to ~/.qai/qai.db.
        qai_dir: Optional ``~/.qai`` directory override. Consumed by the
            managed-listener lifespan reattach logic so tests can route
            state-file reads to a temp dir. ``None`` uses the real
            ``~/.qai``.

    Returns:
        A configured FastAPI instance with routes, templates, and static files.
    """
    app = FastAPI(
        title="q-ai",
        description="Security testing for agentic AI",
        lifespan=_lifespan,
    )

    # Store db_path in app state for route handlers
    app.state.db_path = db_path
    app.state.qai_dir = qai_dir

    # WebSocket connection manager and active workflow tracking
    from q_ai.server.websocket import ConnectionManager

    app.state.ws_manager = ConnectionManager()
    app.state.active_workflows = {}  # dict[str, WorkflowRunner]
    app.state.stranded_runs = {}  # populated by _lifespan on startup
    # Managed-listener registries — populated by _lifespan on startup and
    # mutated by the IPI managed-listener route handlers. Types:
    # dict[str, ManagedListenerHandle] and ForeignListenerRecord | None.
    app.state.managed_listeners = {}
    app.state.foreign_listener = None

    # Cache bridge token at startup so the internal endpoint avoids
    # blocking disk I/O on every request (mirrors ipi/server.py caching).
    from q_ai.core.bridge_token import read_bridge_token

    app.state.bridge_token = read_bridge_token()

    from q_ai.services.run_service import format_age

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["format_status"] = lambda s: s.replace("_", " ").title() if s else s
    templates.env.filters["format_age"] = format_age
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from q_ai.server.routes import (
        admin,
        assist,
        db_ops,
        intel,
        internal,
        runs,
        websocket,
        workflows,
    )
    from q_ai.server.routes.modules import audit, chain, cxp, ipi, proxy, rxp

    for module in (
        runs,
        workflows,
        admin,
        db_ops,
        intel,
        assist,
        websocket,
        internal,
        audit,
        chain,
        cxp,
        ipi,
        proxy,
        rxp,
    ):
        app.include_router(module.router)

    return app
