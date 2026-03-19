"""Tests for the run results view (Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import (
    create_finding,
    create_run,
    create_target,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_completed_assess_run(
    tmp_db: Path,
) -> tuple[str, str, str]:
    """Create a completed assess workflow with audit child run and findings.

    Returns (parent_run_id, audit_child_id, target_id).
    """
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        target_id = create_target(conn, type="server", name="Test Server", uri="stdio://test")
        parent_id = create_run(conn, module="workflow", name="assess", target_id=target_id)
        update_run_status(conn, parent_id, RunStatus.RUNNING)

        audit_child = create_run(conn, module="audit", name="audit-child", parent_run_id=parent_id)
        update_run_status(conn, audit_child, RunStatus.COMPLETED)

        create_finding(
            conn,
            run_id=audit_child,
            module="audit",
            category="command_injection",
            severity=Severity.CRITICAL,
            title="Shell command injection",
            description="The server executes unvalidated input.",
            framework_ids={"OWASP_MCP": ["MCP-01"], "CWE": ["CWE-78"]},
        )
        create_finding(
            conn,
            run_id=audit_child,
            module="audit",
            category="information_disclosure",
            severity=Severity.HIGH,
            title="Verbose error leaks",
            description="Error messages reveal internal paths.",
        )

        update_run_status(conn, parent_id, RunStatus.COMPLETED)
        conn.commit()
        return parent_id, audit_child, target_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rename: Operations → Runs
# ---------------------------------------------------------------------------


class TestOperationsRedirect:
    """GET /operations should 301 redirect to /runs."""

    def test_redirect_no_params(self, client: TestClient) -> None:
        resp = client.get("/operations", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/runs"

    def test_redirect_preserves_query_params(self, client: TestClient) -> None:
        resp = client.get("/operations?run_id=abc&foo=bar", follow_redirects=False)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/runs?run_id=abc&foo=bar"


class TestOverviewHeader:
    """Overview header renders for terminal runs with run_id."""

    def test_overview_header_renders_workflow_name(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert resp.status_code == 200
        assert "Assess an MCP Server" in resp.text

    def test_overview_header_shows_target(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Test Server" in resp.text
        assert "stdio://test" in resp.text

    def test_overview_header_shows_status_badge(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "COMPLETED" in resp.text
        assert "badge-success" in resp.text

    def test_overview_header_shows_finding_counts(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "1 Critical" in resp.text
        assert "1 High" in resp.text

    def test_overview_header_generate_report_button(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Generate Report" in resp.text
        assert f"/api/exports/{parent_id}/report" in resp.text

    def test_overview_header_export_json_placeholder(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Export JSON" in resp.text

    def test_running_run_shows_status_bar_not_header(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.RUNNING)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "operations-status-bar" in resp.text
        assert "overview-header" not in resp.text


class TestRunsPage:
    """GET /runs basic behavior."""

    def test_runs_no_run_id_returns_200(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert "IDLE" in resp.text

    def test_runs_nav_shows_runs_label(self, client: TestClient) -> None:
        resp = client.get("/runs")
        assert ">Runs<" in resp.text.replace(" ", "").replace("\n", "")

    def test_runs_page_passes_run_id(self, client: TestClient) -> None:
        resp = client.get("/runs?run_id=abc")
        assert resp.status_code == 200
        assert 'data-run-id="abc"' in resp.text


class TestScopedModuleTabs:
    """Only tabs for the workflow's modules should appear."""

    def test_assess_shows_only_audit_proxy_inject(self, client: TestClient, tmp_db: Path) -> None:
        parent_id, _, _ = _setup_completed_assess_run(tmp_db)
        resp = client.get(f"/runs?run_id={parent_id}")
        text = resp.text
        assert "onclick=\"switchTab(this, 'audit')\"" in text
        assert "onclick=\"switchTab(this, 'proxy')\"" in text
        assert "onclick=\"switchTab(this, 'inject')\"" in text
        assert "onclick=\"switchTab(this, 'chain')\"" not in text
        assert "onclick=\"switchTab(this, 'ipi')\"" not in text
        assert "onclick=\"switchTab(this, 'cxp')\"" not in text
        assert "onclick=\"switchTab(this, 'rxp')\"" not in text

    def test_no_run_id_shows_all_tabs(self, client: TestClient) -> None:
        resp = client.get("/runs")
        text = resp.text
        for mod in ["audit", "proxy", "inject", "chain", "ipi", "cxp", "rxp"]:
            assert f"onclick=\"switchTab(this, '{mod}')\"" in text

    def test_module_did_not_execute_message(self, client: TestClient, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            parent_id = create_run(conn, module="workflow", name="assess")
            update_run_status(conn, parent_id, RunStatus.PARTIAL)
            audit_child = create_run(
                conn, module="audit", name="audit-child", parent_run_id=parent_id
            )
            update_run_status(conn, audit_child, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()
        resp = client.get(f"/runs?run_id={parent_id}")
        assert "Module did not execute in this run" in resp.text
