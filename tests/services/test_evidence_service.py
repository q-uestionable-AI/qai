"""Tests for the evidence service."""

from __future__ import annotations

import sqlite3

from q_ai.services import evidence_service


class TestListEvidence:
    """Tests for evidence_service.list_evidence()."""

    def test_by_finding(
        self,
        db: sqlite3.Connection,
        sample_findings: list[str],
        sample_evidence: list[str],
    ) -> None:
        """Lists evidence for a specific finding."""
        f1 = sample_findings[0]
        results = evidence_service.list_evidence(db, finding_id=f1)
        assert len(results) == 2
        assert all(e.finding_id == f1 for e in results)

    def test_by_run(
        self,
        db: sqlite3.Connection,
        sample_child_runs: list[str],
        sample_evidence: list[str],
    ) -> None:
        """Lists evidence for a specific run."""
        audit_id = sample_child_runs[0]
        results = evidence_service.list_evidence(db, run_id=audit_id)
        assert len(results) == 1
        assert results[0].run_id == audit_id

    def test_no_results(self, db: sqlite3.Connection) -> None:
        """Returns empty list when no evidence matches."""
        results = evidence_service.list_evidence(db, finding_id="nonexistent")
        assert results == []


class TestGetEvidence:
    """Tests for evidence_service.get_evidence()."""

    def test_returns_evidence(
        self,
        db: sqlite3.Connection,
        sample_evidence: list[str],
    ) -> None:
        """Returns evidence by ID."""
        e1 = sample_evidence[0]
        result = evidence_service.get_evidence(db, e1)
        assert result is not None
        assert result.id == e1
        assert result.type == "request"

    def test_not_found(self, db: sqlite3.Connection) -> None:
        """Returns None for nonexistent evidence ID."""
        assert evidence_service.get_evidence(db, "nonexistent") is None
