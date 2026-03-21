"""Tests for cross-run awareness DB helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from q_ai.core.db import (
    get_connection,
    get_previously_seen_finding_keys,
    get_prior_run_counts_by_target,
)
from q_ai.core.models import RunStatus


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create a temporary database with schema for testing."""
    db = tmp_path / "test.db"
    with get_connection(db):
        pass
    return db


@pytest.fixture()
def conn(db_path: Path) -> sqlite3.Connection:
    """Yield an active connection for testing."""
    with get_connection(db_path) as c:
        yield c


def _insert_target(conn: sqlite3.Connection, target_id: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO targets (id, type, name, created_at)
           VALUES (?, 'mcp_server', ?, ?)""",
        (target_id, target_id, datetime.now(UTC).isoformat()),
    )


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    target_id: str | None,
    status: int,
    started_at: str,
    parent_run_id: str | None = None,
) -> None:
    if target_id is not None:
        _insert_target(conn, target_id)
    if parent_run_id is not None:
        # Insert a stub parent run to satisfy the self-referencing FK.
        conn.execute(
            "INSERT OR IGNORE INTO runs (id, module, status, started_at)"
            " VALUES (?, 'workflow', 0, ?)",
            (parent_run_id, started_at),
        )
    conn.execute(
        """INSERT INTO runs (id, module, target_id, status, started_at, parent_run_id)
           VALUES (?, 'workflow', ?, ?, ?, ?)""",
        (run_id, target_id, status, started_at, parent_run_id),
    )


def _insert_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    run_id: str,
    category: str,
    title: str,
) -> None:
    conn.execute(
        """INSERT INTO findings (id, run_id, module, category, severity, title, created_at)
           VALUES (?, ?, 'audit', ?, 3, ?, ?)""",
        (finding_id, run_id, category, title, datetime.now(UTC).isoformat()),
    )


class TestGetPreviouslySeenFindingKeys:
    def test_returns_keys_from_prior_run(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, "r1", "t1", int(RunStatus.COMPLETED), "2026-01-01T00:00:00")
        _insert_finding(conn, "f1", "r1", "command_injection", "Found vuln")
        _insert_run(conn, "r2", "t1", int(RunStatus.RUNNING), "2026-01-02T00:00:00")

        result = get_previously_seen_finding_keys(conn, "t1", "2026-01-02T00:00:00", "r2")
        assert ("command_injection", "Found vuln") in result

    def test_excludes_current_run(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, "r1", "t1", int(RunStatus.COMPLETED), "2026-01-01T00:00:00")
        _insert_finding(conn, "f1", "r1", "cmd_inj", "Vuln A")

        result = get_previously_seen_finding_keys(conn, "t1", "2026-01-01T00:00:00", "r1")
        assert len(result) == 0

    def test_excludes_child_runs(self, conn: sqlite3.Connection) -> None:
        _insert_run(
            conn,
            "r1",
            "t1",
            int(RunStatus.COMPLETED),
            "2026-01-01T00:00:00",
            parent_run_id="parent1",
        )
        _insert_finding(conn, "f1", "r1", "cmd_inj", "Vuln from child")

        result = get_previously_seen_finding_keys(conn, "t1", "2026-01-02T00:00:00", "r2")
        assert len(result) == 0

    def test_excludes_different_target(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, "r1", "t_other", int(RunStatus.COMPLETED), "2026-01-01T00:00:00")
        _insert_finding(conn, "f1", "r1", "cmd_inj", "Other target")

        result = get_previously_seen_finding_keys(conn, "t1", "2026-01-02T00:00:00", "r2")
        assert len(result) == 0

    def test_includes_partial_runs(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, "r1", "t1", int(RunStatus.PARTIAL), "2026-01-01T00:00:00")
        _insert_finding(conn, "f1", "r1", "cmd_inj", "Partial finding")

        result = get_previously_seen_finding_keys(conn, "t1", "2026-01-02T00:00:00", "r2")
        assert ("cmd_inj", "Partial finding") in result


class TestGetPriorRunCountsByTarget:
    def test_returns_count_for_target(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, "r1", "t1", int(RunStatus.COMPLETED), "2026-01-01T00:00:00")
        _insert_run(conn, "r2", "t1", int(RunStatus.COMPLETED), "2026-01-02T00:00:00")

        result = get_prior_run_counts_by_target(conn, ["t1"])
        assert result["t1"] == 2

    def test_excludes_child_runs(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, "r1", "t1", int(RunStatus.COMPLETED), "2026-01-01T00:00:00")
        _insert_run(
            conn,
            "r2",
            "t1",
            int(RunStatus.COMPLETED),
            "2026-01-02T00:00:00",
            parent_run_id="r1",
        )

        result = get_prior_run_counts_by_target(conn, ["t1"])
        assert result["t1"] == 1

    def test_missing_target_not_in_result(self, conn: sqlite3.Connection) -> None:
        result = get_prior_run_counts_by_target(conn, ["t_nonexistent"])
        assert "t_nonexistent" not in result

    def test_empty_target_ids(self, conn: sqlite3.Connection) -> None:
        result = get_prior_run_counts_by_target(conn, [])
        assert result == {}
