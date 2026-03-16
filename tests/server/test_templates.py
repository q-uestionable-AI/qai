"""Tests for template rendering details."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.core.db import create_finding, create_run, create_target
from q_ai.core.models import Severity


class TestWorkflowCards:
    """Launcher renders all six workflow cards."""

    def test_all_six_cards_present(self, client: TestClient) -> None:
        resp = client.get("/")
        for name in [
            "Assess an MCP Server",
            "Test Document Ingestion",
            "Test a Coding Assistant",
            "Trace an Attack Path",
            "Measure Blast Radius",
            "Generate Report",
        ]:
            assert name in resp.text

    def test_module_pills_present(self, client: TestClient) -> None:
        resp = client.get("/")
        for mod in ["audit", "proxy", "inject", "ipi", "cxp", "rxp", "chain"]:
            assert mod in resp.text

    def test_modal_present(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "modal-assess" in resp.text
        assert "modal-generate_report" in resp.text


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
