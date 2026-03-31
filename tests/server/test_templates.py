"""Tests for template rendering details."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from q_ai.core.db import create_finding, create_run, create_target
from q_ai.core.models import Severity


class TestWorkflowAccordion:
    """Launcher renders accordion rows for all visible workflows."""

    def test_all_visible_rows_present(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        for name in [
            "Assess an MCP Server",
            "Test Document Ingestion",
            "Test Context Poisoning",
            "Trace an Attack Path",
            "Measure Blast Radius",
        ]:
            assert name in resp.text
        # Generate Report hidden from launcher (visible_in_launcher=False)
        assert "Generate Report" not in resp.text

    def test_module_pills_present(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        for mod in ["audit", "proxy", "inject", "ipi", "cxp", "rxp", "chain"]:
            assert mod in resp.text

    def test_accordion_panel_present(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        assert "wf-panel" in resp.text
        assert "wf-row" in resp.text

    def test_all_rows_collapsed_by_default(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        assert 'id="wf-row-assess"' in resp.text
        # No row should have the expanded class in the server-rendered HTML
        assert 'class="wf-row expanded' not in resp.text

    def test_inline_forms_present(self, client: TestClient) -> None:
        # A configured provider is required for the Assess form to render
        with (
            patch("q_ai.server.routes.get_credential", return_value="test-key"),
            patch("q_ai.core.providers.get_credential", return_value="test-key"),
        ):
            resp = client.get("/launcher")
        assert "form-assess" in resp.text
        # generate_report is hidden from launcher, no form expected
        assert "form-generate_report" not in resp.text


class TestEmptyStates:
    """Empty state messages render when DB has no data."""

    def test_runs_empty(self, client: TestClient) -> None:
        resp = client.get("/api/runs")
        assert "No runs" in resp.text

    def test_findings_empty(self, client: TestClient) -> None:
        resp = client.get("/api/findings")
        assert "No findings" in resp.text

    def test_targets_empty(self, client: TestClient) -> None:
        resp = client.get("/api/targets")
        assert "No targets" in resp.text


class TestSeverityBadges:
    """Severity badges render with correct CSS classes."""

    def test_severity_badge_classes(self, tmp_db: Path, client: TestClient) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id = create_target(conn, type="server", name="test-srv")
            run_id = create_run(conn, module="audit", target_id=target_id)
            for sev in Severity:
                create_finding(
                    conn,
                    run_id=run_id,
                    module="audit",
                    category="test",
                    severity=sev,
                    title=f"Finding {sev.name}",
                )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/findings")
        assert "CRITICAL" in resp.text
        assert "HIGH" in resp.text
        assert "MEDIUM" in resp.text
        assert "LOW" in resp.text
        assert "INFO" in resp.text


class TestFrameworkIdBadges:
    """Framework ID badges render in findings table."""

    def test_framework_id_badges_in_findings(self, tmp_db: Path, client: TestClient) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            target_id = create_target(conn, type="server", name="badge-srv")
            run_id = create_run(conn, module="audit", target_id=target_id)
            create_finding(
                conn,
                run_id=run_id,
                module="audit",
                category="tool_poisoning",
                severity=Severity.HIGH,
                title="Poisoned tool detected",
                framework_ids={
                    "owasp_mcp_top10": "MCP03",
                    "owasp_agentic_top10": "ASI02",
                    "mitre_atlas": ["AML.T0051.000", "AML.T0080"],
                    "cwe": ["CWE-94", "CWE-74"],
                },
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/findings")
        assert "MCP03" in resp.text
        assert "ASI02" in resp.text
        assert "AML.T0051.000" in resp.text
        assert "AML.T0080" in resp.text
        assert "CWE-94" in resp.text
        assert "CWE-74" in resp.text


class TestResearchWithData:
    """Research tables render data from the database."""

    def test_runs_table_with_data(self, tmp_db: Path, client: TestClient) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            create_run(conn, module="audit", name="test-run")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/runs")
        assert "audit" in resp.text
        assert "test-run" in resp.text

    def test_targets_table_with_data(self, tmp_db: Path, client: TestClient) -> None:
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            create_target(conn, type="server", name="my-server", uri="http://localhost:3000")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/api/targets")
        assert "my-server" in resp.text
        assert "http://localhost:3000" in resp.text
