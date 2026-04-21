"""Tests for the Phase 5 synthetic Unbound migration in :mod:`q_ai.server.app`.

The migration reparents historical NULL-``target_id`` runs to a synthetic
``type='virtual'`` target carrying ``metadata.kind = 'synthetic-unbound'``.
These tests exercise the migration function directly rather than via
``_lifespan`` end-to-end — unit-level assertions on idempotency, row
reparenting, and collision behavior with a user-created same-name target.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from q_ai.core.db import create_run, create_target, get_connection
from q_ai.server.app import _migrate_unbound_runs


def _open_db(path: Path) -> sqlite3.Connection:
    """Open the test DB with FK + row factory on."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _insert_null_run(conn: sqlite3.Connection, *, module: str = "import") -> str:
    """Insert a run with NULL target_id and return its id."""
    return create_run(conn, module=module, name=f"{module}-null-run")


def _get_unbound_target(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, name, type, metadata FROM targets "
        "WHERE json_extract(metadata, '$.kind') = 'synthetic-unbound' LIMIT 1"
    ).fetchone()


class TestMigrateUnboundRuns:
    """`_migrate_unbound_runs` creates the synthetic target and reparents."""

    def test_first_call_creates_target_and_reparents(self, tmp_db: Path) -> None:
        with get_connection(tmp_db) as conn:
            run_a = _insert_null_run(conn, module="import")
            run_b = _insert_null_run(conn, module="ipi-probe")
            run_c = _insert_null_run(conn, module="workflow")
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            row = _get_unbound_target(conn)
            assert row is not None
            assert row["name"] == "(Unbound historical intel)"
            assert row["type"] == "virtual"
            assert json.loads(row["metadata"]) == {"kind": "synthetic-unbound"}
            target_id = row["id"]

            # All three null-target_id runs reparented.
            for rid in (run_a, run_b, run_c):
                r = conn.execute("SELECT target_id FROM runs WHERE id = ?", (rid,)).fetchone()
                assert r["target_id"] == target_id
        finally:
            conn.close()

    def test_idempotent_on_second_call(self, tmp_db: Path) -> None:
        _migrate_unbound_runs(tmp_db)
        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            rows = conn.execute(
                "SELECT id FROM targets "
                "WHERE json_extract(metadata, '$.kind') = 'synthetic-unbound'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            conn.close()

    def test_zero_null_runs_still_creates_target(self, tmp_db: Path) -> None:
        """Fresh DB with no NULL rows still produces the synthetic target."""
        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            row = _get_unbound_target(conn)
            assert row is not None
        finally:
            conn.close()

    def test_preserves_user_target_with_same_literal_name(self, tmp_db: Path) -> None:
        """A user target named literally "(Unbound historical intel)" is left alone.

        The existence check keys on ``metadata.kind`` — a user row with the
        same display name but no kind flag is distinct from the synthetic.
        """
        with get_connection(tmp_db) as conn:
            user_id = create_target(conn, type="server", name="(Unbound historical intel)")
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            rows = conn.execute(
                "SELECT id, metadata FROM targets WHERE name = ?",
                ("(Unbound historical intel)",),
            ).fetchall()
            assert len(rows) == 2
            # The user row has no metadata kind flag; the synthetic one does.
            kinds = [json.loads(r["metadata"]).get("kind") if r["metadata"] else None for r in rows]
            assert "synthetic-unbound" in kinds
            assert None in kinds or any(k != "synthetic-unbound" for k in kinds)
            # The user target is untouched.
            user_row = conn.execute(
                "SELECT metadata FROM targets WHERE id = ?", (user_id,)
            ).fetchone()
            assert user_row["metadata"] is None or "synthetic-unbound" not in (
                user_row["metadata"] or ""
            )
        finally:
            conn.close()

    def test_preserves_config_bound_target_on_null_row(self, tmp_db: Path) -> None:
        """Workflow parent runs store target_id in config, not runs.target_id.

        The migration must first promote those rows using the config-bound
        id before bucketing the remainder into Unbound — see PR #131 review
        (CodeRabbit) and the two-step UPDATE in ``_migrate_unbound_runs``.
        """
        import json as _json

        with get_connection(tmp_db) as conn:
            real_target = create_target(conn, type="server", name="real-workflow-target")
            # Parent workflow run shape: NULL runs.target_id, target_id in config.
            workflow_run = create_run(
                conn,
                module="workflow",
                name="assess",
                config={"target_id": real_target, "other": "data"},
            )
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            row = conn.execute(
                "SELECT target_id, config FROM runs WHERE id = ?", (workflow_run,)
            ).fetchone()
            # Runs row was promoted to the real target — NOT the synthetic Unbound.
            assert row["target_id"] == real_target
            # config is untouched.
            assert _json.loads(row["config"])["target_id"] == real_target
        finally:
            conn.close()

    def test_null_config_target_still_reparents_to_unbound(self, tmp_db: Path) -> None:
        """Runs with NULL target_id AND no config.target_id fall into Unbound.

        Covers both the "config is NULL" shape and the "config exists but has
        no target_id key" shape. Mirrors pre-Phase 5 import/probe/sweep rows
        that lack any target binding at all.
        """
        with get_connection(tmp_db) as conn:
            null_config_run = create_run(conn, module="import", name="no-config")
            empty_config_run = create_run(
                conn, module="import", name="empty-config", config={"other": "data"}
            )
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            synthetic = _get_unbound_target(conn)
            assert synthetic is not None
            for rid in (null_config_run, empty_config_run):
                r = conn.execute("SELECT target_id FROM runs WHERE id = ?", (rid,)).fetchone()
                assert r["target_id"] == synthetic["id"]
        finally:
            conn.close()

    def test_orphan_config_target_falls_through_to_unbound(self, tmp_db: Path) -> None:
        """config.target_id pointing at a non-existent target falls to Unbound.

        The backfill UPDATE guards with ``IN (SELECT id FROM targets)`` so a
        stale / orphaned reference does not introduce a FK violation.
        """
        with get_connection(tmp_db) as conn:
            orphan_run = create_run(
                conn,
                module="workflow",
                name="orphan",
                config={"target_id": "nonexistent-target-id"},
            )
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            synthetic = _get_unbound_target(conn)
            assert synthetic is not None
            row = conn.execute("SELECT target_id FROM runs WHERE id = ?", (orphan_run,)).fetchone()
            assert row["target_id"] == synthetic["id"]
        finally:
            conn.close()

    def test_does_not_retarget_runs_already_bound(self, tmp_db: Path) -> None:
        """Runs with a non-NULL target_id are not touched."""
        with get_connection(tmp_db) as conn:
            bound_target = create_target(conn, type="server", name="bound-target")
            bound_run = create_run(conn, module="import", name="bound-run", target_id=bound_target)
            null_run = _insert_null_run(conn)
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            bound_row = conn.execute(
                "SELECT target_id FROM runs WHERE id = ?", (bound_run,)
            ).fetchone()
            null_row = conn.execute(
                "SELECT target_id FROM runs WHERE id = ?", (null_run,)
            ).fetchone()
            assert bound_row["target_id"] == bound_target
            synthetic = _get_unbound_target(conn)
            assert synthetic is not None
            assert null_row["target_id"] == synthetic["id"]
        finally:
            conn.close()

    def test_all_rows_bound_is_noop(self, tmp_db: Path) -> None:
        """When every run has target_id set, the migration leaves bindings intact.

        WA2 closes the forward path so new workflow runs are born with
        ``runs.target_id`` populated. This test locks in that scenario —
        a DB where all rows are already bound — as a supported no-op at
        the end-state level: no NULL rows remain and every binding is
        preserved.
        """
        with get_connection(tmp_db) as conn:
            t1 = create_target(conn, type="server", name="t1")
            t2 = create_target(conn, type="server", name="t2")
            create_run(conn, module="workflow", name="w1", target_id=t1)
            create_run(conn, module="import", name="i1", target_id=t2)
            conn.commit()

        _migrate_unbound_runs(tmp_db)

        conn = _open_db(tmp_db)
        try:
            null_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE target_id IS NULL"
            ).fetchone()
            assert null_rows["n"] == 0
            bound_rows = conn.execute(
                "SELECT target_id FROM runs WHERE module IN ('workflow', 'import')"
            ).fetchall()
            assert {r["target_id"] for r in bound_rows} == {t1, t2}
        finally:
            conn.close()
