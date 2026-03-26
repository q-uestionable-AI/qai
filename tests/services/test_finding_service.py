"""Tests for the finding service."""

from __future__ import annotations

import sqlite3

from q_ai.core.models import Severity
from q_ai.services import finding_service


class TestListFindings:
    """Tests for finding_service.list_findings()."""

    def test_list_all(self, db: sqlite3.Connection, sample_findings: list[str]) -> None:
        """Returns all findings when no filters applied."""
        results = finding_service.list_findings(db)
        assert len(results) == 3

    def test_filter_by_module(self, db: sqlite3.Connection, sample_findings: list[str]) -> None:
        """Filters findings by module name."""
        results = finding_service.list_findings(db, module="audit")
        assert len(results) == 2
        assert all(f.module == "audit" for f in results)

    def test_filter_by_category(self, db: sqlite3.Connection, sample_findings: list[str]) -> None:
        """Filters findings by category."""
        results = finding_service.list_findings(db, category="tool_poisoning")
        assert len(results) == 1
        assert results[0].category == "tool_poisoning"

    def test_filter_by_min_severity(
        self, db: sqlite3.Connection, sample_findings: list[str]
    ) -> None:
        """Filters findings at or above minimum severity."""
        results = finding_service.list_findings(db, min_severity=Severity.HIGH)
        assert len(results) == 2
        assert all(f.severity >= Severity.HIGH for f in results)

    def test_filter_by_run_id(
        self,
        db: sqlite3.Connection,
        sample_findings: list[str],
        sample_child_runs: list[str],
    ) -> None:
        """Filters findings by a single run ID."""
        inject_id = sample_child_runs[1]
        results = finding_service.list_findings(db, run_id=inject_id)
        assert len(results) == 1
        assert results[0].module == "inject"

    def test_empty_result(self, db: sqlite3.Connection) -> None:
        """Returns empty list when no findings match."""
        results = finding_service.list_findings(db, module="nonexistent")
        assert results == []


class TestGetFinding:
    """Tests for finding_service.get_finding()."""

    def test_returns_finding_with_evidence(
        self,
        db: sqlite3.Connection,
        sample_findings: list[str],
        sample_evidence: list[str],
    ) -> None:
        """Returns finding and its associated evidence."""
        f1 = sample_findings[0]
        result = finding_service.get_finding(db, f1)
        assert result is not None
        finding, evidence = result
        assert finding.id == f1
        assert finding.category == "command_injection"
        assert len(evidence) == 2

    def test_finding_no_evidence(self, db: sqlite3.Connection, sample_findings: list[str]) -> None:
        """Returns finding with empty evidence list when none attached."""
        f3 = sample_findings[2]  # inject finding, no evidence
        result = finding_service.get_finding(db, f3)
        assert result is not None
        finding, evidence = result
        assert finding.id == f3
        assert evidence == []

    def test_not_found(self, db: sqlite3.Connection) -> None:
        """Returns None for nonexistent finding ID."""
        assert finding_service.get_finding(db, "nonexistent") is None


class TestGetFindingsForRun:
    """Tests for finding_service.get_findings_for_run()."""

    def test_includes_child_run_findings(
        self,
        db: sqlite3.Connection,
        sample_run: str,
        sample_findings: list[str],
    ) -> None:
        """Returns findings from parent and all child runs."""
        from q_ai.core.db import create_finding

        # Add a finding directly on the parent run to verify both halves
        parent_finding = create_finding(
            db,
            run_id=sample_run,
            module="workflow",
            category="overview",
            severity=Severity.INFO,
            title="Parent-level finding",
        )
        results = finding_service.get_findings_for_run(db, sample_run)
        assert len(results) == 4
        result_ids = {f.id for f in results}
        assert parent_finding in result_ids

    def test_no_findings(self, db: sqlite3.Connection, sample_run: str) -> None:
        """Returns empty list for run with no findings."""
        # sample_run has no direct findings (they're on children)
        # but get_findings_for_run includes child runs, so create a bare run
        from q_ai.core.db import create_run

        bare_id = create_run(db, module="workflow", name="empty")
        results = finding_service.get_findings_for_run(db, bare_id)
        assert results == []
