"""Tests for the run service."""

from __future__ import annotations

import datetime as _dt
import sqlite3

from q_ai.core.db import create_run, create_target, update_run_status
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


def _completed_run(
    db: sqlite3.Connection,
    *,
    module: str,
    target_id: str,
    finished_at: str,
) -> str:
    """Create a run already in COMPLETED with a pinned finished_at."""
    run_id = create_run(db, module=module, target_id=target_id)
    update_run_status(db, run_id, RunStatus.COMPLETED, finished_at=finished_at)
    return run_id


class TestQueryTargetsOverview:
    """Tests for run_service.query_targets_overview()."""

    def test_zero_targets_returns_empty(self, db: sqlite3.Connection) -> None:
        """Zero targets → empty rows."""
        result = run_service.query_targets_overview(db)
        assert result.rows == []

    def test_target_with_no_runs_still_produces_row(self, db: sqlite3.Connection) -> None:
        """A target without any runs produces a row with all three latests None."""
        target_id = create_target(db, type="server", name="alpha")
        result = run_service.query_targets_overview(db)
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.target.id == target_id
        assert row.latest_probe_finished_at is None
        assert row.latest_sweep_finished_at is None
        assert row.latest_import_finished_at is None

    def test_one_target_one_run_per_module(self, db: sqlite3.Connection) -> None:
        """One completed run of each tracked module populates all three latests."""
        target_id = create_target(db, type="server", name="beta")
        _completed_run(
            db, module="ipi-probe", target_id=target_id, finished_at="2026-04-10T12:00:00+00:00"
        )
        _completed_run(
            db, module="ipi-sweep", target_id=target_id, finished_at="2026-04-11T12:00:00+00:00"
        )
        _completed_run(
            db, module="import", target_id=target_id, finished_at="2026-04-12T12:00:00+00:00"
        )

        result = run_service.query_targets_overview(db)
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.latest_probe_finished_at == _dt.datetime(2026, 4, 10, 12, 0, 0, tzinfo=_dt.UTC)
        assert row.latest_sweep_finished_at == _dt.datetime(2026, 4, 11, 12, 0, 0, tzinfo=_dt.UTC)
        assert row.latest_import_finished_at == _dt.datetime(2026, 4, 12, 12, 0, 0, tzinfo=_dt.UTC)

    def test_ignores_non_completed_runs(self, db: sqlite3.Connection) -> None:
        """A RUNNING probe after a COMPLETED one does not win; completed wins."""
        target_id = create_target(db, type="server", name="gamma")
        _completed_run(
            db, module="ipi-probe", target_id=target_id, finished_at="2026-04-10T12:00:00+00:00"
        )
        running_id = create_run(db, module="ipi-probe", target_id=target_id)
        update_run_status(db, running_id, RunStatus.RUNNING)

        result = run_service.query_targets_overview(db)
        row = result.rows[0]
        assert row.latest_probe_finished_at == _dt.datetime(2026, 4, 10, 12, 0, 0, tzinfo=_dt.UTC)

    def test_ignores_other_modules(self, db: sqlite3.Connection) -> None:
        """Runs with module='ipi' or 'audit' do not populate any latest field."""
        target_id = create_target(db, type="server", name="delta")
        _completed_run(
            db, module="ipi", target_id=target_id, finished_at="2026-04-10T12:00:00+00:00"
        )
        _completed_run(
            db, module="audit", target_id=target_id, finished_at="2026-04-11T12:00:00+00:00"
        )

        result = run_service.query_targets_overview(db)
        row = result.rows[0]
        assert row.latest_probe_finished_at is None
        assert row.latest_sweep_finished_at is None
        assert row.latest_import_finished_at is None

    def test_latest_wins_across_multiple_completed(self, db: sqlite3.Connection) -> None:
        """Given multiple completed sweeps, the greatest finished_at wins."""
        target_id = create_target(db, type="server", name="epsilon")
        _completed_run(
            db, module="ipi-sweep", target_id=target_id, finished_at="2026-04-01T12:00:00+00:00"
        )
        _completed_run(
            db, module="ipi-sweep", target_id=target_id, finished_at="2026-04-15T12:00:00+00:00"
        )
        _completed_run(
            db, module="ipi-sweep", target_id=target_id, finished_at="2026-04-05T12:00:00+00:00"
        )

        result = run_service.query_targets_overview(db)
        row = result.rows[0]
        assert row.latest_sweep_finished_at == _dt.datetime(2026, 4, 15, 12, 0, 0, tzinfo=_dt.UTC)

    def test_multiple_targets_each_isolated(self, db: sqlite3.Connection) -> None:
        """Each target's latest fields are scoped to its own runs."""
        t1 = create_target(db, type="server", name="a-target")
        t2 = create_target(db, type="server", name="b-target")
        _completed_run(
            db, module="ipi-probe", target_id=t1, finished_at="2026-04-10T12:00:00+00:00"
        )
        _completed_run(db, module="import", target_id=t2, finished_at="2026-04-11T12:00:00+00:00")

        result = run_service.query_targets_overview(db)
        by_id = {r.target.id: r for r in result.rows}
        assert by_id[t1].latest_probe_finished_at is not None
        assert by_id[t1].latest_import_finished_at is None
        assert by_id[t2].latest_probe_finished_at is None
        assert by_id[t2].latest_import_finished_at is not None


class TestQueryTargetOverviewById:
    """Tests for run_service.query_target_overview_by_id()."""

    def test_returns_none_for_missing(self, db: sqlite3.Connection) -> None:
        """Missing target ID returns None."""
        assert run_service.query_target_overview_by_id(db, "does-not-exist") is None

    def test_returns_row_for_valid_target(self, db: sqlite3.Connection) -> None:
        """Valid target returns a row with correct latests."""
        target_id = create_target(db, type="server", name="zed")
        _completed_run(
            db, module="ipi-probe", target_id=target_id, finished_at="2026-04-10T12:00:00+00:00"
        )

        row = run_service.query_target_overview_by_id(db, target_id)
        assert row is not None
        assert row.target.id == target_id
        assert row.latest_probe_finished_at == _dt.datetime(2026, 4, 10, 12, 0, 0, tzinfo=_dt.UTC)
        assert row.latest_sweep_finished_at is None
        assert row.latest_import_finished_at is None


class TestFormatAge:
    """Tests for run_service.format_age()."""

    _NOW = _dt.datetime(2026, 4, 19, 12, 0, 0, tzinfo=_dt.UTC)

    def test_none_returns_em_dash(self) -> None:
        assert run_service.format_age(None) == "\u2014"

    def test_sub_minute_returns_now(self) -> None:
        dt = self._NOW - _dt.timedelta(seconds=30)
        assert run_service.format_age(dt, now=self._NOW) == "now"

    def test_future_returns_now(self) -> None:
        dt = self._NOW + _dt.timedelta(minutes=5)
        assert run_service.format_age(dt, now=self._NOW) == "now"

    def test_minutes(self) -> None:
        dt = self._NOW - _dt.timedelta(minutes=5)
        assert run_service.format_age(dt, now=self._NOW) == "5m"

    def test_minutes_upper_boundary(self) -> None:
        dt = self._NOW - _dt.timedelta(minutes=59)
        assert run_service.format_age(dt, now=self._NOW) == "59m"

    def test_hours(self) -> None:
        dt = self._NOW - _dt.timedelta(hours=3)
        assert run_service.format_age(dt, now=self._NOW) == "3h"

    def test_hours_upper_boundary(self) -> None:
        dt = self._NOW - _dt.timedelta(hours=23, minutes=59)
        assert run_service.format_age(dt, now=self._NOW) == "23h"

    def test_days(self) -> None:
        dt = self._NOW - _dt.timedelta(days=2)
        assert run_service.format_age(dt, now=self._NOW) == "2d"

    def test_very_old(self) -> None:
        dt = self._NOW - _dt.timedelta(days=1000)
        assert run_service.format_age(dt, now=self._NOW) == "1000d"

    def test_naive_dt_treated_as_utc(self) -> None:
        """Naive datetimes are assumed to be UTC (matches DB convention)."""
        dt = (self._NOW - _dt.timedelta(hours=2)).replace(tzinfo=None)
        assert run_service.format_age(dt, now=self._NOW) == "2h"
