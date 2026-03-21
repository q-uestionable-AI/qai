"""Tests for CSV export format."""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from q_ai.audit.reporting.csv_report import generate_csv_report
from q_ai.core.mitigation import (
    GuidanceSection,
    MitigationGuidance,
    SectionKind,
    SourceType,
)
from tests.audit.test_reporting import FakeScanResult, _make_finding


class TestCsvReport:
    def test_header_row_present(self) -> None:
        """CSV output has expected column headers."""
        scan = FakeScanResult(findings=[_make_finding()])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_csv_report(scan, Path(tmp_dir) / "out.csv")
            reader = csv.DictReader(io.StringIO(path.read_text()))
            assert "category" in reader.fieldnames
            assert "severity" in reader.fieldnames
            assert "mitigation_summary" in reader.fieldnames

    def test_one_row_per_finding(self) -> None:
        """Each finding produces exactly one data row."""
        f1 = _make_finding(title="Finding 1")
        f2 = _make_finding(title="Finding 2")
        scan = FakeScanResult(findings=[f1, f2])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_csv_report(scan, Path(tmp_dir) / "out.csv")
            reader = csv.DictReader(io.StringIO(path.read_text()))
            rows = list(reader)
            assert len(rows) == 2

    def test_mitigation_summary_single_taxonomy(self) -> None:
        """Single taxonomy section shows first action."""
        guidance = MitigationGuidance(
            sections=[
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=["owasp_mcp_top10"],
                    items=["Validate inputs", "Second action"],
                ),
            ],
        )
        finding = _make_finding(mitigation=guidance)
        scan = FakeScanResult(findings=[finding])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_csv_report(scan, Path(tmp_dir) / "out.csv")
            reader = csv.DictReader(io.StringIO(path.read_text()))
            row = next(reader)
            assert row["mitigation_summary"] == "Validate inputs"

    def test_mitigation_summary_multiple_sections(self) -> None:
        """Multiple sections show 'See full report'."""
        guidance = MitigationGuidance(
            sections=[
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.TAXONOMY,
                    source_ids=["owasp_mcp_top10"],
                    items=["Action 1"],
                ),
                GuidanceSection(
                    kind=SectionKind.ACTIONS,
                    source_type=SourceType.RULE,
                    source_ids=["cwe:command_injection"],
                    items=["Action 2"],
                ),
            ],
        )
        finding = _make_finding(mitigation=guidance)
        scan = FakeScanResult(findings=[finding])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_csv_report(scan, Path(tmp_dir) / "out.csv")
            reader = csv.DictReader(io.StringIO(path.read_text()))
            row = next(reader)
            assert row["mitigation_summary"] == "See full report for details"

    def test_mitigation_summary_legacy_empty(self) -> None:
        """Legacy finding (None mitigation) has empty summary."""
        finding = _make_finding(mitigation=None)
        scan = FakeScanResult(findings=[finding])

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = generate_csv_report(scan, Path(tmp_dir) / "out.csv")
            reader = csv.DictReader(io.StringIO(path.read_text()))
            row = next(reader)
            assert row["mitigation_summary"] == ""
