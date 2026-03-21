"""Tests for NDJSON export format."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from q_ai.audit.reporting.ndjson_report import generate_ndjson_report
from q_ai.core.mitigation import (
    GuidanceSection,
    MitigationGuidance,
    SectionKind,
    SourceType,
)
from tests.audit.test_reporting import FakeScanResult, _make_finding


class TestNdjsonReport:
    def test_one_line_per_finding(self) -> None:
        """Each finding produces exactly one JSON line."""
        f1 = _make_finding(title="Finding 1")
        f2 = _make_finding(title="Finding 2")
        scan = FakeScanResult(findings=[f1, f2])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(scan, Path(tmp_dir) / "out.ndjson")
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 2

    def test_each_line_is_valid_json(self) -> None:
        """Each line parses as valid JSON."""
        scan = FakeScanResult(findings=[_make_finding()])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(scan, Path(tmp_dir) / "out.ndjson")
            for line in path.read_text().strip().split("\n"):
                obj = json.loads(line)
                assert "title" in obj

    def test_includes_run_metadata(self) -> None:
        """Each line includes run metadata when provided."""
        scan = FakeScanResult(findings=[_make_finding()])
        meta = {
            "run_id": "r1",
            "started_at": "2026-01-01T00:00:00",
            "target_name": "test-srv",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(scan, Path(tmp_dir) / "out.ndjson", run_metadata=meta)
            obj = json.loads(path.read_text().strip())
            assert obj["run_id"] == "r1"
            assert obj["target_name"] == "test-srv"

    def test_includes_mitigation(self) -> None:
        """Mitigation is serialized in each line."""
        guidance = MitigationGuidance(
            sections=[
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=["owasp_mcp_top10"],
                    items=["Validate inputs"],
                ),
            ],
        )
        finding = _make_finding(mitigation=guidance)
        scan = FakeScanResult(findings=[finding])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(scan, Path(tmp_dir) / "out.ndjson")
            obj = json.loads(path.read_text().strip())
            assert obj["mitigation"]["sections"][0]["kind"] == "actions"

    def test_null_mitigation(self) -> None:
        """None mitigation serializes as null."""
        scan = FakeScanResult(findings=[_make_finding(mitigation=None)])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(scan, Path(tmp_dir) / "out.ndjson")
            obj = json.loads(path.read_text().strip())
            assert obj["mitigation"] is None

    def test_empty_findings_produces_empty_file(self) -> None:
        """No findings produces an empty file."""
        scan = FakeScanResult(findings=[])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(scan, Path(tmp_dir) / "out.ndjson")
            assert path.read_text() == ""

    def test_accepts_finding_list(self) -> None:
        """Generator accepts list[Finding] as well as ScanResult."""
        from q_ai.core.models import Finding
        from q_ai.core.models import Severity as CoreSeverity

        findings = [
            Finding(
                id="f1",
                run_id="r1",
                module="audit",
                category="command_injection",
                severity=CoreSeverity(3),
                title="Test finding",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_ndjson_report(findings, Path(tmp_dir) / "out.ndjson")
            obj = json.loads(path.read_text().strip())
            assert obj["title"] == "Test finding"
