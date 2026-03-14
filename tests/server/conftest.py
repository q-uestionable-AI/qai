"""Shared fixtures for server tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from q_ai.core.schema import migrate


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
