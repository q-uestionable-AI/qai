"""Tests for the operations page with DB-driven state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import create_finding, create_run, update_run_status
from q_ai.core.models import RunStatus, Severity


class TestOperationsNoRunId:
    """Operations page without a run_id parameter."""

    def test_operations_no_run_id(self, client: TestClient) -> None:
        """GET /operations without run_id -> 200, shows IDLE."""
        resp = client.get("/operations")
        assert resp.status_code == 200
        assert "IDLE" in resp.text


class TestOperationsWithRunId:
    """Operations page with a valid run_id parameter."""

    def test_operations_with_run_id(self, client: TestClient, tmp_db: Path) -> None:
        """Create a run in DB, GET /operations?run_id=... -> status badge rendered."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, run_id, RunStatus.RUNNING)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/operations?run_id={run_id}")
        assert resp.status_code == 200
        assert "RUNNING" in resp.text


class TestOperationsWithChildRuns:
    """Operations page with parent + child runs."""

    def test_operations_with_child_runs(self, client: TestClient, tmp_db: Path) -> None:
        """Create parent + child runs, verify child run badges appear."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            child_id = create_run(conn, module="audit", name="audit-child", parent_run_id=parent_id)
            update_run_status(conn, child_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/operations?run_id={parent_id}")
        assert resp.status_code == 200
        assert "audit" in resp.text
        assert "completed" in resp.text


class TestOperationsUnknownRunId:
    """Operations page with an unknown run_id."""

    def test_operations_unknown_run_id(self, client: TestClient) -> None:
        """GET /operations?run_id=nonexistent -> 200, graceful fallback (IDLE)."""
        resp = client.get("/operations?run_id=nonexistent")
        assert resp.status_code == 200
        assert "IDLE" in resp.text


class TestOperationsPassesRunId:
    """Operations page passes run_id to template as data attribute."""

    def test_operations_page_passes_run_id_to_template(self, client: TestClient) -> None:
        """GET /operations?run_id=abc -> data-run-id="abc" in HTML."""
        resp = client.get("/operations?run_id=abc")
        assert resp.status_code == 200
        assert 'data-run-id="abc"' in resp.text


class TestStatusBarPartial:
    """GET /api/operations/status-bar partial route."""

    def test_status_bar_partial_no_children(self, client: TestClient, tmp_db: Path) -> None:
        """Status bar partial with run_id that has no children -> 200, empty."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="workflow", name="assess")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/operations/status-bar?run_id={run_id}")
        assert resp.status_code == 200
        # No child runs, so no badge content
        assert "badge" not in resp.text

    def test_status_bar_partial_with_children(self, client: TestClient, tmp_db: Path) -> None:
        """Status bar partial with parent + child runs -> badges rendered."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            child_id = create_run(conn, module="audit", name="audit-child", parent_run_id=parent_id)
            update_run_status(conn, child_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/operations/status-bar?run_id={parent_id}")
        assert resp.status_code == 200
        assert "audit" in resp.text
        assert "completed" in resp.text


class TestFindingsSidebarPartial:
    """GET /api/operations/findings-sidebar partial route."""

    def test_findings_sidebar_partial_empty(self, client: TestClient, tmp_db: Path) -> None:
        """Findings sidebar with no findings -> 'No findings yet.'"""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="workflow", name="assess")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/operations/findings-sidebar?run_id={run_id}")
        assert resp.status_code == 200
        assert "No findings yet" in resp.text

    def test_findings_sidebar_partial_with_findings(self, client: TestClient, tmp_db: Path) -> None:
        """Findings sidebar with findings -> finding titles rendered."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            run_id = create_run(conn, module="audit", name="audit-run")
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="command_injection",
                severity=Severity.HIGH,
                title="Unsafe shell exec",
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/api/operations/findings-sidebar?run_id={run_id}")
        assert resp.status_code == 200
        assert "Unsafe shell exec" in resp.text
        assert "audit" in resp.text
