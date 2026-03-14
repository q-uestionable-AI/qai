"""Database connection manager and CRUD helpers for q-ai."""

from __future__ import annotations

import datetime
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from q_ai.core.models import (
    Finding,
    Run,
    RunStatus,
    Severity,
    Target,
    _dump_json,
)
from q_ai.core.schema import migrate

_DEFAULT_DB_PATH = Path.home() / ".qai" / "qai.db"


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def _new_id() -> str:
    """Generate a new UUID hex string."""
    return uuid.uuid4().hex


@contextmanager
def get_connection(
    db_path: Path | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a sqlite3 connection with WAL and FK enforcement.

    Creates the database file and parent directories if they don't exist.
    Runs schema migration on first connect.

    Args:
        db_path: Path to database file. Defaults to ~/.qai/qai.db.

    Yields:
        A sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = db_path or _DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
        conn.commit()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run CRUD
# ---------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    module: str,
    name: str | None = None,
    target_id: str | None = None,
    parent_run_id: str | None = None,
    config: dict | None = None,
) -> str:
    """Insert a new run and return its ID.

    Args:
        conn: Active database connection.
        module: Name of the module that owns this run.
        name: Optional human-readable run name.
        target_id: Optional target reference.
        parent_run_id: Optional parent run for chained runs.
        config: Optional configuration dict.

    Returns:
        The hex UUID of the newly created run.
    """
    run_id = _new_id()
    conn.execute(
        """
        INSERT INTO runs
            (id, module, name, target_id, parent_run_id,
             config, status, started_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            module,
            name,
            target_id,
            parent_run_id,
            _dump_json(config),
            int(RunStatus.PENDING),
            _now_iso(),
        ),
    )
    return run_id


def update_run_status(
    conn: sqlite3.Connection,
    run_id: str,
    status: RunStatus,
    finished_at: str | None = None,
) -> None:
    """Update the status of a run.

    Automatically sets finished_at for terminal statuses
    (COMPLETED, FAILED, CANCELLED) if not provided.

    Args:
        conn: Active database connection.
        run_id: ID of the run to update.
        status: New status value.
        finished_at: Optional ISO timestamp override.
    """
    terminal = {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
    if finished_at is None and status in terminal:
        finished_at = _now_iso()
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
        (int(status), finished_at, run_id),
    )


def list_runs(
    conn: sqlite3.Connection,
    module: str | None = None,
    status: RunStatus | None = None,
    target_id: str | None = None,
    parent_run_id: str | None = None,
) -> list[Run]:
    """List runs with optional filters.

    Args:
        conn: Active database connection.
        module: Filter by module name.
        status: Filter by run status.
        target_id: Filter by target ID.
        parent_run_id: Filter by parent run ID.

    Returns:
        List of Run objects ordered by started_at descending.
    """
    query = "SELECT * FROM runs"
    conditions: list[str] = []
    params: list[object] = []

    if module is not None:
        conditions.append("module = ?")
        params.append(module)
    if status is not None:
        conditions.append("status = ?")
        params.append(int(status))
    if target_id is not None:
        conditions.append("target_id = ?")
        params.append(target_id)
    if parent_run_id is not None:
        conditions.append("parent_run_id = ?")
        params.append(parent_run_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC"

    rows = conn.execute(query, params).fetchall()
    return [Run.from_row(dict(row)) for row in rows]


# ---------------------------------------------------------------------------
# Target CRUD
# ---------------------------------------------------------------------------


def create_target(
    conn: sqlite3.Connection,
    type: str,
    name: str,
    uri: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Insert a new target and return its ID.

    Args:
        conn: Active database connection.
        type: Target type (e.g. "server", "endpoint").
        name: Human-readable target name.
        uri: Optional URI or address.
        metadata: Optional metadata dict.

    Returns:
        The hex UUID of the newly created target.
    """
    target_id = _new_id()
    conn.execute(
        """
        INSERT INTO targets (id, type, name, uri, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            target_id,
            type,
            name,
            uri,
            _dump_json(metadata),
            _now_iso(),
        ),
    )
    return target_id


def get_target(
    conn: sqlite3.Connection,
    target_id: str,
) -> Target | None:
    """Retrieve a target by ID.

    Args:
        conn: Active database connection.
        target_id: ID of the target to retrieve.

    Returns:
        A Target instance or None if not found.
    """
    row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        return None
    return Target.from_row(dict(row))


def list_targets(conn: sqlite3.Connection) -> list[Target]:
    """List all targets ordered by creation date descending.

    Args:
        conn: Active database connection.

    Returns:
        List of Target objects.
    """
    rows = conn.execute("SELECT * FROM targets ORDER BY created_at DESC").fetchall()
    return [Target.from_row(dict(row)) for row in rows]


# ---------------------------------------------------------------------------
# Finding CRUD
# ---------------------------------------------------------------------------


def create_finding(
    conn: sqlite3.Connection,
    run_id: str,
    module: str,
    category: str,
    severity: Severity,
    title: str,
    description: str | None = None,
    framework_ids: dict | None = None,
    source_ref: str | None = None,
) -> str:
    """Insert a new finding and return its ID.

    Args:
        conn: Active database connection.
        run_id: ID of the run that produced this finding.
        module: Name of the producing module.
        category: Finding category (e.g. "command_injection").
        severity: Severity level.
        title: Short human-readable title.
        description: Optional detailed description.
        framework_ids: Optional framework identifier mapping.
        source_ref: Optional source reference.

    Returns:
        The hex UUID of the newly created finding.
    """
    finding_id = _new_id()
    conn.execute(
        """
        INSERT INTO findings
            (id, run_id, module, category, severity,
             title, description, framework_ids,
             source_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding_id,
            run_id,
            module,
            category,
            int(severity),
            title,
            description,
            _dump_json(framework_ids),
            source_ref,
            _now_iso(),
        ),
    )
    return finding_id


def list_findings(
    conn: sqlite3.Connection,
    module: str | None = None,
    category: str | None = None,
    min_severity: Severity | None = None,
    run_id: str | None = None,
    target_id: str | None = None,
) -> list[Finding]:
    """List findings with optional filters.

    Args:
        conn: Active database connection.
        module: Filter by module name.
        category: Filter by finding category.
        min_severity: Filter findings at or above this severity.
        run_id: Filter by run ID.
        target_id: Filter by target ID (joins on runs table).

    Returns:
        List of Finding objects ordered by severity DESC,
        created_at DESC.
    """
    needs_join = target_id is not None
    if needs_join:
        query = "SELECT f.* FROM findings f JOIN runs r ON f.run_id = r.id"
    else:
        query = "SELECT * FROM findings"

    conditions: list[str] = []
    params: list[object] = []
    prefix = "f." if needs_join else ""

    if module is not None:
        conditions.append(f"{prefix}module = ?")
        params.append(module)
    if category is not None:
        conditions.append(f"{prefix}category = ?")
        params.append(category)
    if min_severity is not None:
        conditions.append(f"{prefix}severity >= ?")
        params.append(int(min_severity))
    if run_id is not None:
        conditions.append(f"{prefix}run_id = ?")
        params.append(run_id)
    if target_id is not None:
        conditions.append("r.target_id = ?")
        params.append(target_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += f" ORDER BY {prefix}severity DESC, {prefix}created_at DESC"

    rows = conn.execute(query, params).fetchall()
    return [Finding.from_row(dict(row)) for row in rows]


# ---------------------------------------------------------------------------
# Evidence CRUD
# ---------------------------------------------------------------------------


def create_evidence(
    conn: sqlite3.Connection,
    type: str,
    finding_id: str | None = None,
    run_id: str | None = None,
    mime_type: str | None = None,
    hash: str | None = None,
    storage: str = "inline",
    content: str | None = None,
    path: str | None = None,
) -> str:
    """Insert a new evidence record and return its ID.

    Args:
        conn: Active database connection.
        type: Evidence type (e.g. "request", "response").
        finding_id: Optional associated finding ID.
        run_id: Optional associated run ID.
        mime_type: Optional MIME type of the content.
        hash: Optional content hash for integrity.
        storage: Storage mode - "inline" or "file".
        content: Optional inline content.
        path: Optional file path for file-based storage.

    Returns:
        The hex UUID of the newly created evidence.
    """
    evidence_id = _new_id()
    conn.execute(
        """
        INSERT INTO evidence
            (id, type, finding_id, run_id, mime_type,
             hash, storage, content, path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            type,
            finding_id,
            run_id,
            mime_type,
            hash,
            storage,
            content,
            path,
            _now_iso(),
        ),
    )
    return evidence_id


# ---------------------------------------------------------------------------
# Settings CRUD
# ---------------------------------------------------------------------------


def get_setting(
    conn: sqlite3.Connection,
    key: str,
    default: str | None = None,
) -> str | None:
    """Retrieve a setting value by key.

    Args:
        conn: Active database connection.
        key: Setting key to look up.
        default: Value to return if the key is not found.

    Returns:
        The setting value, or default if not found.
    """
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    result: str = row[0]
    return result


def set_setting(
    conn: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    """Create or update a setting.

    Uses INSERT ... ON CONFLICT DO UPDATE to upsert.

    Args:
        conn: Active database connection.
        key: Setting key.
        value: Setting value.
    """
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE
            SET value = excluded.value,
                updated_at = excluded.updated_at
        """,
        (key, value, _now_iso()),
    )
