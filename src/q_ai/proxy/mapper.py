"""Mapper from proxy session to core DB for persistence.

Bridges the proxy session domain to the core persistence domain
so proxy session metadata is stored in the unified SQLite database.
Session capture data (full message log) is saved as JSON under
~/.qai/artifacts/{run_id}/.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus
from q_ai.proxy.session_store import SessionStore

_ARTIFACTS_DIR = Path.home() / ".qai" / "artifacts"


def persist_session(
    store: SessionStore,
    db_path: Path | None = None,
    duration_seconds: float | None = None,
    artifacts_dir: Path | None = None,
    run_id: str | None = None,
    source: str | None = None,
) -> str:
    """Persist a proxy session to the database and artifacts.

    Creates a run record, a proxy_sessions row, and saves the session
    JSON to ~/.qai/artifacts/{run_id}/session.json.

    Args:
        store: The completed session store with captured messages.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.
        duration_seconds: Session duration in seconds. Computed from
            store timestamps if not provided.
        artifacts_dir: Directory for session artifacts. Defaults to
            ~/.qai/artifacts.
        run_id: Optional pre-created run ID from the orchestrator.
            When provided, skips creating a new run row.
        source: Optional provenance tag (e.g. "web", "cli").

    Returns:
        The run ID for the persisted session.
    """
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()

    # Compute duration from store timestamps if not provided
    if duration_seconds is None:
        messages = store.get_messages()
        if messages:
            first_ts = messages[0].timestamp
            last_ts = messages[-1].timestamp
            duration_seconds = (last_ts - first_ts).total_seconds()
        else:
            duration_seconds = 0.0

    # Determine server name (command for stdio, URL for HTTP transports)
    server_name = store.server_command or store.server_url or ""

    # Save session JSON to artifacts
    if artifacts_dir is None:
        artifacts_dir = _ARTIFACTS_DIR

    with get_connection(db_path) as conn:
        # Create run record (unless pre-created by orchestrator)
        if run_id is None:
            run_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO runs
                    (id, module, name, target_id, parent_run_id,
                     config, status, started_at, finished_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "proxy",
                    f"proxy-{store.transport.value}",
                    None,
                    None,
                    None,
                    int(RunStatus.COMPLETED),
                    store.started_at.isoformat(),
                    now_iso,
                    source,
                ),
            )

        session_rel_path = f"{run_id}/session.json"
        session_abs_path = artifacts_dir / session_rel_path
        store.save(session_abs_path)

        # Create proxy_sessions record
        conn.execute(
            """
            INSERT INTO proxy_sessions
                (id, run_id, transport, server_name, message_count,
                 duration_seconds, session_file, created_at,
                 chain_run_id, chain_step_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                run_id,
                store.transport.value,
                server_name,
                len(store.get_messages()),
                duration_seconds,
                session_rel_path,
                now_iso,
                store.chain_run_id,
                store.chain_step_id,
            ),
        )

    return run_id
