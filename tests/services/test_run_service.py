"""Tests for the run service."""

from __future__ import annotations

import sqlite3

from q_ai.core.db import create_run, update_run_status
from q_ai.core.models import RunStatus
from q_ai.services import run_service


class TestGetRun:
    """Tests for run_service.get_run()."""

    def test_returns_run(self, db: sqlite3.Connection, sample_run: str) -> None:
        """Returns a Run for a valid ID."""
        run = run_service.get_run(db, sample_run)
        assert run is not None
        assert run.id == sample_run
        assert run.module == "workflow"

    def test_not_found(self, db: sqlite3.Connection) -> None:
        """Returns None for nonexistent ID."""
        assert run_service.get_run(db, "nonexistent") is None


class TestListRuns:
    """Tests for run_service.list_runs()."""

    def test_list_all(
        self, db: sqlite3.Connection, sample_run: str, sample_child_runs: list[str]
    ) -> None:
        """Returns all runs when no filters applied."""
        results = run_service.list_runs(db)
        assert len(results) == 3  # parent + 2 children

    def test_filter_by_module(
        self, db: sqlite3.Connection, sample_run: str, sample_child_runs: list[str]
    ) -> None:
        """Filters runs by module."""
        results = run_service.list_runs(db, module="audit")
        assert len(results) == 1
        assert results[0].module == "audit"

    def test_filter_by_status(self, db: sqlite3.Connection) -> None:
        """Filters runs by status."""
        run_id = create_run(db, module="test")
        update_run_status(db, run_id, RunStatus.RUNNING)
        results = run_service.list_runs(db, status=RunStatus.RUNNING)
        assert len(results) == 1
        assert results[0].id == run_id

    def test_filter_by_name(self, db: sqlite3.Connection, sample_run: str) -> None:
        """Filters runs by name."""
        results = run_service.list_runs(db, name="assess")
        assert len(results) == 1
        assert results[0].id == sample_run


class TestGetChildRuns:
    """Tests for run_service.get_child_runs()."""

    def test_returns_children(
        self, db: sqlite3.Connection, sample_run: str, sample_child_runs: list[str]
    ) -> None:
        """Returns child runs for a parent."""
        children = run_service.get_child_runs(db, sample_run)
        assert len(children) == 2
        child_ids = {c.id for c in children}
        assert child_ids == set(sample_child_runs)

    def test_no_children(self, db: sqlite3.Connection) -> None:
        """Returns empty list for run with no children."""
        run_id = create_run(db, module="test")
        assert run_service.get_child_runs(db, run_id) == []


class TestGetRunWithChildren:
    """Tests for run_service.get_run_with_children()."""

    def test_returns_parent_and_children(
        self, db: sqlite3.Connection, sample_run: str, sample_child_runs: list[str]
    ) -> None:
        """Returns parent run and its children."""
        parent, children = run_service.get_run_with_children(db, sample_run)
        assert parent is not None
        assert parent.id == sample_run
        assert len(children) == 2

    def test_not_found(self, db: sqlite3.Connection) -> None:
        """Returns (None, []) for nonexistent run."""
        parent, children = run_service.get_run_with_children(db, "nonexistent")
        assert parent is None
        assert children == []


class TestGetFindingCountForRuns:
    """Tests for run_service.get_finding_count_for_runs()."""

    def test_counts_findings(
        self,
        db: sqlite3.Connection,
        sample_child_runs: list[str],
        sample_findings: list[str],
    ) -> None:
        """Counts findings across multiple run IDs."""
        count = run_service.get_finding_count_for_runs(db, sample_child_runs)
        assert count == 3

    def test_empty_ids(self, db: sqlite3.Connection) -> None:
        """Returns 0 for empty run ID list."""
        assert run_service.get_finding_count_for_runs(db, []) == 0


class TestGetChildRunIds:
    """Tests for run_service.get_child_run_ids()."""

    def test_returns_ids(
        self, db: sqlite3.Connection, sample_run: str, sample_child_runs: list[str]
    ) -> None:
        """Returns child run IDs as strings."""
        ids = run_service.get_child_run_ids(db, sample_run)
        assert set(ids) == set(sample_child_runs)


class TestSourceField:
    """Tests for run source provenance."""

    def test_source_persisted(self, db: sqlite3.Connection) -> None:
        """Source field is stored and retrieved correctly."""
        run_id = create_run(db, module="test", source="web")
        run = run_service.get_run(db, run_id)
        assert run is not None
        assert run.source == "web"

    def test_source_cli(self, db: sqlite3.Connection) -> None:
        """CLI source value persists correctly."""
        run_id = create_run(db, module="test", source="cli")
        run = run_service.get_run(db, run_id)
        assert run is not None
        assert run.source == "cli"

    def test_null_source_backwards_compat(self, db: sqlite3.Connection) -> None:
        """Runs with NULL source load without error."""
        run_id = create_run(db, module="test")
        run = run_service.get_run(db, run_id)
        assert run is not None
        assert run.source is None

    def test_source_in_to_dict(self, db: sqlite3.Connection) -> None:
        """Source field appears in to_dict() output."""
        run_id = create_run(db, module="test", source="api")
        run = run_service.get_run(db, run_id)
        assert run is not None
        d = run.to_dict()
        assert d["source"] == "api"

    def test_source_inherited_by_child(self, db: sqlite3.Connection) -> None:
        """Demonstrates source can be set on child runs independently."""
        parent_id = create_run(db, module="workflow", source="web")
        child_id = create_run(db, module="audit", parent_run_id=parent_id, source="cli")
        child = run_service.get_run(db, child_id)
        assert child is not None
        assert child.source == "cli"
