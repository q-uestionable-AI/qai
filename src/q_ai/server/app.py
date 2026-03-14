"""FastAPI application factory for the q-ai web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI


def create_app(db_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Optional database path override. Defaults to ~/.qai/qai.db.

    Returns:
        A configured FastAPI instance with routes, templates, and static files.
    """
    app = FastAPI(title="q-ai")
    return app
