"""Shared fixtures for server tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from q_ai.core.schema import migrate
from q_ai.server.app import create_app


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with schema applied."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def client(tmp_db: Path) -> Generator[TestClient, None, None]:
    """Create a test client with a temporary database."""
    app = create_app(db_path=tmp_db)
    with TestClient(app) as c:
        yield c
