"""Destructive database operations — backup and reset."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from q_ai.core.db import _DEFAULT_DB_PATH, get_connection
from q_ai.server.routes._shared import _get_db_path
from q_ai.services import db_service

router = APIRouter()


def _sync_backup_database(db_path: Path | None) -> str:
    """Create a database backup (blocking).

    Args:
        db_path: Path to the SQLite database.

    Returns:
        String path to the created backup file.
    """
    source = db_path or _DEFAULT_DB_PATH
    result = db_service.backup_database(source)
    return str(result)


@router.post("/api/db/backup", response_model=None)
async def api_db_backup(request: Request) -> JSONResponse:
    """Create a backup of the database.

    Args:
        request: The incoming HTTP request.

    Returns:
        JSON with status and backup path.
    """
    db_path = _get_db_path(request)
    try:
        path = await asyncio.to_thread(_sync_backup_database, db_path)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "Database file not found"})
    return JSONResponse(content={"status": "created", "path": path})


def _sync_reset_database(db_path: Path | None) -> str | None:
    """Reset the database (blocking).

    Args:
        db_path: Path to the SQLite database.

    Returns:
        String path to the backup file, or None.
    """
    source = db_path or _DEFAULT_DB_PATH
    with get_connection(db_path) as conn:
        backup_path = db_service.reset_database(conn, source)
    return str(backup_path) if backup_path else None


@router.post("/api/db/reset", response_model=None)
async def api_db_reset(request: Request) -> JSONResponse:
    """Reset the database — delete all operational data.

    Settings and credentials are preserved. A backup is created first.

    Args:
        request: The incoming HTTP request.

    Returns:
        JSON with status and backup_path.
    """
    db_path = _get_db_path(request)
    backup_path = await asyncio.to_thread(_sync_reset_database, db_path)
    return JSONResponse(content={"status": "reset", "backup_path": backup_path})
