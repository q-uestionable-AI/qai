"""Tests for the audit mapper."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from q_ai.audit.mapper import _build_description, _map_severity, persist_scan
from q_ai.audit.orchestrator import ScanResult
from q_ai.core.db import create_run, create_target, get_connection
from q_ai.core.mitigation import GuidanceSection, MitigationGuidance, SectionKind, SourceType
from q_ai.core.models import Severity as CoreSeverity
from q_ai.core.schema import migrate
from q_ai.mcp.models import ScanFinding, Severity


class TestMapSeverity:
    def test_all_levels(self) -> None:
        """Verify all severity levels map correctly by name."""
        assert _map_severity(Severity.CRITICAL) == CoreSeverity.CRITICAL
        assert _map_severity(Severity.HIGH) == CoreSeverity.HIGH
        assert _map_severity(Severity.MEDIUM) == CoreSeverity.MEDIUM
        assert _map_severity(Severity.LOW) == CoreSeverity.LOW
        assert _map_severity(Severity.INFO) == CoreSeverity.INFO

    def test_int_values(self) -> None:
        """Verify mapped severity has expected int values."""
        assert int(_map_severity(Severity.CRITICAL)) == 4
        assert int(_map_severity(Severity.INFO)) == 0


class TestBuildDescription:
    def test_combines_all_fields(self) -> None:
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Test",
            description="Main description",
            severity=Severity.HIGH,
            tool_name="my_tool",
            evidence="some evidence",
            remediation="fix it",
        )
        result = _build_description(finding)
        assert "Main description" in result
        assert "Tool: my_tool" in result
        assert "Evidence: some evidence" in result
        assert "Remediation: fix it" in result

    def test_empty_optional_fields(self) -> None:
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Test",
            description="Only description",
            severity=Severity.LOW,
        )
        result = _build_description(finding)
        assert result == "Only description"

    def test_no_description(self) -> None:
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Test",
            description="",
            severity=Severity.LOW,
            tool_name="tool1",
        )
        result = _build_description(finding)
        assert "Tool: tool1" in result
        assert result.startswith("Tool:")


class TestPersistScan:
    def _make_db(self) -> Path:
        """Create a temporary database with schema."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            pass
        db_path = Path(tmp.name)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
        conn.commit()
        conn.close()
        return db_path

    def test_persist_with_explicit_run_id(self) -> None:
        """Verify persist_scan uses provided run_id instead of creating a new one."""
        db_path = self._make_db()
        # Pre-create a run
        with get_connection(db_path) as conn:
            pre_run_id = create_run(conn, module="audit", name="pre-created")

        scan_result = ScanResult(
            findings=[],
            server_info={"name": "test-server", "version": "1.0"},
            tools_scanned=0,
            scanners_run=["injection"],
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        )

        run_id = persist_scan(scan_result, db_path=db_path, run_id=pre_run_id)
        assert run_id == pre_run_id

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Should only have the pre-created run, not a second one
        all_runs = conn.execute("SELECT * FROM runs").fetchall()
        assert len(all_runs) == 1
        assert all_runs[0]["id"] == pre_run_id

        # Audit scan row should reference the pre-created run
        scans = conn.execute("SELECT * FROM audit_scans").fetchall()
        assert len(scans) == 1
        assert scans[0]["run_id"] == pre_run_id
        conn.close()

    def test_persist_with_explicit_target_id(self) -> None:
        """Verify persist_scan uses provided target_id instead of creating a new one."""
        db_path = self._make_db()
        with get_connection(db_path) as conn:
            pre_target_id = create_target(conn, type="server", name="pre-target")

        scan_result = ScanResult(
            findings=[],
            server_info={"name": "test-server", "version": "1.0"},
            tools_scanned=0,
            scanners_run=["injection"],
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        )

        persist_scan(scan_result, db_path=db_path, target_id=pre_target_id)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        targets = conn.execute("SELECT * FROM targets").fetchall()
        # Only the pre-created target should exist
        assert len(targets) == 1
        assert targets[0]["id"] == pre_target_id
        conn.close()

    def test_persist_creates_run_findings_audit_scan(self) -> None:
        """Verify that persist_scan creates target, run, findings, and audit_scans rows."""
        db_path = self._make_db()

        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Injection found",
            description="Found injection in tool",
            severity=Severity.HIGH,
            tool_name="my_tool",
            evidence="payload response",
            remediation="sanitize input",
            framework_ids={"owasp_mcp_top10": "MCP05"},
        )

        scan_result = ScanResult(
            findings=[finding],
            server_info={"name": "test-server", "version": "1.0"},
            tools_scanned=5,
            scanners_run=["injection"],
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )

        run_id = persist_scan(scan_result, db_path=db_path, transport="stdio")
        assert run_id

        # Verify database contents
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Check target
        targets = conn.execute("SELECT * FROM targets").fetchall()
        assert len(targets) == 1
        assert dict(targets[0])["name"] == "test-server"

        # Check run
        runs = conn.execute("SELECT * FROM runs").fetchall()
        assert len(runs) == 1
        run = dict(runs[0])
        assert run["id"] == run_id
        assert run["module"] == "audit"
        assert run["status"] == 2  # COMPLETED

        # Check findings
        findings = conn.execute("SELECT * FROM findings").fetchall()
        assert len(findings) == 1
        f = dict(findings[0])
        assert f["run_id"] == run_id
        assert f["category"] == "command_injection"
        assert f["severity"] == int(CoreSeverity.HIGH)
        assert f["title"] == "Injection found"
        assert "Injection found" not in f["description"]  # description != title
        assert "Found injection in tool" in f["description"]
        fwids = json.loads(f["framework_ids"])
        assert fwids["owasp_mcp_top10"] == "MCP05"

        # Check audit_scans
        scans = conn.execute("SELECT * FROM audit_scans").fetchall()
        assert len(scans) == 1
        scan = dict(scans[0])
        assert scan["run_id"] == run_id
        assert scan["transport"] == "stdio"
        assert scan["server_name"] == "test-server"
        assert scan["finding_count"] == 1
        assert scan["scan_duration_seconds"] == pytest.approx(60.0)

        conn.close()

    def test_persist_no_findings(self) -> None:
        """Verify persist_scan works with zero findings."""
        db_path = self._make_db()

        scan_result = ScanResult(
            findings=[],
            server_info={"name": "empty-server", "version": "1.0"},
            tools_scanned=0,
            scanners_run=["injection"],
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        )

        run_id = persist_scan(scan_result, db_path=db_path)
        assert run_id

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        findings = conn.execute("SELECT * FROM findings").fetchall()
        assert len(findings) == 0

        scans = conn.execute("SELECT * FROM audit_scans").fetchall()
        assert len(scans) == 1
        assert dict(scans[0])["finding_count"] == 0
        conn.close()

    def test_persist_mitigation_as_json(self) -> None:
        """Verify mitigation guidance is persisted as JSON in the mitigation column."""
        db_path = self._make_db()

        guidance = MitigationGuidance(
            sections=[
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=["owasp_mcp_top10"],
                    items=["Sanitize inputs"],
                ),
            ],
            caveats=["Test caveat"],
        )

        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Injection found",
            description="Found injection",
            severity=Severity.HIGH,
            framework_ids={"owasp_mcp_top10": "MCP05"},
            mitigation=guidance,
        )

        scan_result = ScanResult(
            findings=[finding],
            server_info={"name": "test-server", "version": "1.0"},
            tools_scanned=1,
            scanners_run=["injection"],
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        )

        persist_scan(scan_result, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        findings = conn.execute("SELECT * FROM findings").fetchall()
        assert len(findings) == 1
        f = dict(findings[0])
        assert f["mitigation"] is not None
        restored = MitigationGuidance.from_dict(json.loads(f["mitigation"]))
        assert len(restored.sections) == 1
        assert restored.sections[0].items == ["Sanitize inputs"]
        assert restored.caveats == ["Test caveat"]
        conn.close()
