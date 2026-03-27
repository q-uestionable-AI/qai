"""Tests for the finding service."""

from __future__ import annotations

import sqlite3

from q_ai.core.models import RunStatus, Severity
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


class TestGetImportedFindingsForTarget:
    """Tests for finding_service.get_imported_findings_for_target()."""

    def test_returns_imported_findings_for_target(self, db: sqlite3.Connection) -> None:
        """Returns findings from import runs matching target_id."""
        from q_ai.core.db import create_finding, create_run, update_run_status

        # Create a target
        db.execute(
            "INSERT INTO targets (id, type, name, created_at) VALUES (?, ?, ?, ?)",
            ("tgt-1", "server", "test-target", "2026-01-01T00:00:00"),
        )

        # Create an import run with target_id
        import_id = create_run(
            db,
            module="import",
            name="garak-import",
            target_id="tgt-1",
            source="garak",
        )
        update_run_status(db, import_id, RunStatus.COMPLETED)
        create_finding(
            db,
            run_id=import_id,
            module="garak",
            category="prompt_injection",
            severity=Severity.HIGH,
            title="imported finding",
        )

        results = finding_service.get_imported_findings_for_target(db, "tgt-1")
        assert len(results) == 1
        assert results[0].category == "prompt_injection"

    def test_excludes_specified_run_ids(self, db: sqlite3.Connection) -> None:
        """Findings from excluded run IDs are not returned."""
        from q_ai.core.db import create_finding, create_run, update_run_status

        db.execute(
            "INSERT INTO targets (id, type, name, created_at) VALUES (?, ?, ?, ?)",
            ("tgt-2", "server", "test-target-2", "2026-01-01T00:00:00"),
        )

        import_id = create_run(
            db,
            module="import",
            name="import-1",
            target_id="tgt-2",
            source="garak",
        )
        update_run_status(db, import_id, RunStatus.COMPLETED)
        create_finding(
            db,
            run_id=import_id,
            module="garak",
            category="data_leak",
            severity=Severity.MEDIUM,
            title="should be excluded",
        )

        results = finding_service.get_imported_findings_for_target(
            db, "tgt-2", exclude_run_ids=[import_id]
        )
        assert results == []

    def test_ignores_non_import_runs(self, db: sqlite3.Connection) -> None:
        """Findings from non-import runs (e.g. audit) are not returned."""
        from q_ai.core.db import create_finding, create_run, update_run_status

        db.execute(
            "INSERT INTO targets (id, type, name, created_at) VALUES (?, ?, ?, ?)",
            ("tgt-3", "server", "test-target-3", "2026-01-01T00:00:00"),
        )

        audit_id = create_run(
            db,
            module="audit",
            name="audit-run",
            target_id="tgt-3",
            source="web",
        )
        update_run_status(db, audit_id, RunStatus.COMPLETED)
        create_finding(
            db,
            run_id=audit_id,
            module="audit",
            category="command_injection",
            severity=Severity.HIGH,
            title="native finding",
        )

        results = finding_service.get_imported_findings_for_target(db, "tgt-3")
        assert results == []

    def test_empty_when_no_imports(self, db: sqlite3.Connection) -> None:
        """Returns empty list when no import runs exist for target."""
        results = finding_service.get_imported_findings_for_target(db, "no-such-target")
        assert results == []
