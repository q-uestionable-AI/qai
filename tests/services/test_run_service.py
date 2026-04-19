"""Tests for the run service."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3

from q_ai.core.db import create_evidence, create_run, create_target, update_run_status
from q_ai.core.models import RunStatus, Severity
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


def _seed_sweep_metadata(
    db: sqlite3.Connection,
    run_id: str,
    *,
    template_count: int,
    style_count: int,
    total_cases: int,
) -> None:
    """Seed a matching ipi_sweep_metadata evidence blob for a sweep run."""
    blob = {
        "total_cases": total_cases,
        "total_complied": 0,
        "overall_compliance_rate": 0.0,
        "overall_severity": "INFO",
        "template_summary": {
            f"template_{i}": {"total": 1, "complied": 0, "rate": 0.0, "severity": "INFO"}
            for i in range(template_count)
        },
        "style_summary": {
            f"style_{i}": {"total": 1, "complied": 0, "rate": 0.0, "severity": "INFO"}
            for i in range(style_count)
        },
        "combination_summary": [],
    }
    import json

    create_evidence(
        db,
        type="ipi_sweep_metadata",
        run_id=run_id,
        storage="inline",
        content=json.dumps(blob),
    )


class TestQueryTargetSweepRuns:
    """Tests for run_service.query_target_sweep_runs()."""

    def test_zero_runs_returns_empty(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="empty")
        assert run_service.query_target_sweep_runs(db, target_id) == []

    def test_one_completed_row_with_aggregates(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="one-sweep")
        run_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_sweep_metadata(db, run_id, template_count=3, style_count=2, total_cases=24)

        rows = run_service.query_target_sweep_runs(db, target_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.run_id == run_id
        assert row.status == RunStatus.COMPLETED
        assert row.finished_at == _dt.datetime(2026, 4, 10, 12, 0, 0, tzinfo=_dt.UTC)
        assert row.template_count == 3
        assert row.style_count == 2
        assert row.reps == 4  # 24 / (3 * 2)
        assert row.total_cases == 24
        assert row.metadata_available is True

    def test_ordering_is_finished_at_desc(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="multi-sweep")
        earlier = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-01T12:00:00+00:00",
        )
        latest = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-15T12:00:00+00:00",
        )
        mid = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-05T12:00:00+00:00",
        )
        for rid in (earlier, latest, mid):
            _seed_sweep_metadata(db, rid, template_count=1, style_count=1, total_cases=1)

        rows = run_service.query_target_sweep_runs(db, target_id)
        assert [r.run_id for r in rows] == [latest, mid, earlier]

    def test_excludes_other_modules(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="cross-module")
        sweep_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_sweep_metadata(db, sweep_id, template_count=1, style_count=1, total_cases=1)
        _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-11T12:00:00+00:00",
        )
        _completed_run(
            db,
            module="import",
            target_id=target_id,
            finished_at="2026-04-12T12:00:00+00:00",
        )
        _completed_run(
            db,
            module="ipi",
            target_id=target_id,
            finished_at="2026-04-13T12:00:00+00:00",
        )

        rows = run_service.query_target_sweep_runs(db, target_id)
        assert [r.run_id for r in rows] == [sweep_id]

    def test_excludes_other_targets(self, db: sqlite3.Connection) -> None:
        t1 = create_target(db, type="server", name="t1")
        t2 = create_target(db, type="server", name="t2")
        own = _completed_run(
            db, module="ipi-sweep", target_id=t1, finished_at="2026-04-10T12:00:00+00:00"
        )
        _seed_sweep_metadata(db, own, template_count=1, style_count=1, total_cases=1)
        other = _completed_run(
            db, module="ipi-sweep", target_id=t2, finished_at="2026-04-11T12:00:00+00:00"
        )
        _seed_sweep_metadata(db, other, template_count=1, style_count=1, total_cases=1)

        assert [r.run_id for r in run_service.query_target_sweep_runs(db, t1)] == [own]

    def test_row_without_metadata_renders_with_flag(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="no-metadata")
        run_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        # No evidence seeded.

        rows = run_service.query_target_sweep_runs(db, target_id)
        assert len(rows) == 1
        assert rows[0].run_id == run_id
        assert rows[0].metadata_available is False
        assert rows[0].template_count == 0
        assert rows[0].style_count == 0
        assert rows[0].reps is None
        assert rows[0].total_cases == 0


class TestExtractSweepRunSummary:
    """Tests for run_service.extract_sweep_run_summary()."""

    def test_valid_blob_populates_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="valid")
        run_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_sweep_metadata(db, run_id, template_count=5, style_count=4, total_cases=60)

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_sweep_run_summary(db, run)
        assert summary.metadata_available is True
        assert summary.template_count == 5
        assert summary.style_count == 4
        assert summary.total_cases == 60
        assert summary.reps == 3  # 60 / (5 * 4)

    def test_missing_blob_returns_flagged_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="absent")
        run_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_sweep_run_summary(db, run)
        assert summary.metadata_available is False
        assert summary.reps is None
        assert summary.template_count == 0

    def test_malformed_json_returns_flagged_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="malformed")
        run_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        create_evidence(
            db,
            type="ipi_sweep_metadata",
            run_id=run_id,
            storage="inline",
            content="not json {{{",
        )

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_sweep_run_summary(db, run)
        assert summary.metadata_available is False
        assert summary.template_count == 0
        assert summary.style_count == 0

    def test_zero_combinations_returns_none_reps(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="zero-combos")
        run_id = _completed_run(
            db,
            module="ipi-sweep",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_sweep_metadata(db, run_id, template_count=0, style_count=0, total_cases=0)

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_sweep_run_summary(db, run)
        assert summary.metadata_available is True
        assert summary.reps is None


# ---------------------------------------------------------------------------
# Probe-run summary tests (Phase 3)
# ---------------------------------------------------------------------------


def _seed_probe_metadata(
    db: sqlite3.Connection,
    run_id: str,
    *,
    total_probes: int = 20,
    total_complied: int = 4,
    overall_rate: float = 0.2,
    overall_severity: str = "MEDIUM",
    categories: int = 3,
) -> None:
    """Seed a matching ipi_probe_metadata evidence blob for a probe run."""
    blob = {
        "model": "test-model",
        "endpoint": "http://localhost:8000/v1",
        "total_probes": total_probes,
        "total_complied": total_complied,
        "overall_compliance_rate": overall_rate,
        "overall_severity": overall_severity,
        "category_summary": {
            f"cat_{i}": {
                "total": 1,
                "complied": 0,
                "rate": 0.0,
                "severity": "INFO",
            }
            for i in range(categories)
        },
    }
    create_evidence(
        db,
        type="ipi_probe_metadata",
        run_id=run_id,
        storage="inline",
        content=json.dumps(blob),
    )


def _seed_probe_metadata_raw(
    db: sqlite3.Connection,
    run_id: str,
    content: str,
) -> None:
    """Seed the probe metadata evidence row with an arbitrary content string."""
    create_evidence(
        db,
        type="ipi_probe_metadata",
        run_id=run_id,
        storage="inline",
        content=content,
    )


class TestExtractProbeRunSummary:
    """Tests for run_service.extract_probe_run_summary()."""

    def test_valid_blob_populates_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="valid-probe")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata(
            db,
            run_id,
            total_probes=20,
            total_complied=5,
            overall_rate=0.25,
            overall_severity="HIGH",
            categories=8,
        )

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is True
        assert summary.total_probes == 20
        assert summary.total_complied == 5
        assert summary.overall_compliance_rate == 0.25
        assert summary.overall_severity is Severity.HIGH
        assert summary.category_count == 8

    def test_missing_blob_returns_flagged_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="absent-probe")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False
        assert summary.total_probes == 0
        assert summary.total_complied == 0
        assert summary.overall_compliance_rate == 0.0
        assert summary.overall_severity is Severity.INFO
        assert summary.category_count == 0

    def test_malformed_json_returns_flagged_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="malformed-probe")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata_raw(db, run_id, "not json {{{")

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False
        assert summary.total_probes == 0

    def test_missing_category_summary_key(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="missing-cats")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata_raw(
            db,
            run_id,
            json.dumps({"overall_severity": "HIGH", "total_probes": 20}),
        )

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False
        assert summary.category_count == 0

    def test_missing_overall_severity_key(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="missing-sev")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata_raw(
            db,
            run_id,
            json.dumps({"category_summary": {"a": {}}, "total_probes": 5}),
        )

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False
        assert summary.overall_severity is Severity.INFO

    def test_unknown_severity_name(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="unknown-sev")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata(db, run_id, overall_severity="SUPER_CRITICAL")

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False

    def test_type_unexpected_category_summary(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="weird-cats")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata_raw(
            db,
            run_id,
            json.dumps(
                {
                    "overall_severity": "HIGH",
                    "category_summary": ["not", "a", "dict"],
                    "total_probes": 5,
                }
            ),
        )

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False

    def test_non_numeric_rate_defaults_without_raising(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="bad-rate")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata_raw(
            db,
            run_id,
            json.dumps(
                {
                    "overall_severity": "HIGH",
                    "category_summary": {"a": {}},
                    "total_probes": "twenty",
                    "total_complied": None,
                    "overall_compliance_rate": "high",
                }
            ),
        )

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        # Blob is present and the required fields parse — metadata_available
        # is True; only the type-unexpected numerics fall back to defaults.
        assert summary.metadata_available is True
        assert summary.total_probes == 0
        assert summary.total_complied == 0
        assert summary.overall_compliance_rate == 0.0
        assert summary.overall_severity is Severity.HIGH

    def test_blob_that_is_a_json_list_not_dict(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="list-blob")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata_raw(db, run_id, json.dumps([1, 2, 3]))

        run = run_service.get_run(db, run_id)
        assert run is not None
        summary = run_service.extract_probe_run_summary(db, run)
        assert summary.metadata_available is False


class TestQueryTargetProbeRuns:
    """Tests for run_service.query_target_probe_runs()."""

    def test_zero_runs_returns_empty(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="empty-probe")
        assert run_service.query_target_probe_runs(db, target_id) == []

    def test_one_completed_row_with_aggregates(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="one-probe")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _seed_probe_metadata(db, run_id, overall_severity="HIGH", categories=5)

        rows = run_service.query_target_probe_runs(db, target_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.run_id == run_id
        assert row.status == RunStatus.COMPLETED
        assert row.finished_at == _dt.datetime(2026, 4, 10, 12, 0, 0, tzinfo=_dt.UTC)
        assert row.overall_severity is Severity.HIGH
        assert row.category_count == 5
        assert row.metadata_available is True

    def test_ordering_is_finished_at_desc_with_none_last(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="multi-probe")
        earlier = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-01T12:00:00+00:00",
        )
        latest = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-15T12:00:00+00:00",
        )
        mid = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-05T12:00:00+00:00",
        )
        # Running probe (no finished_at) must sort last.
        running = create_run(db, module="ipi-probe", target_id=target_id)
        update_run_status(db, running, RunStatus.RUNNING)

        rows = run_service.query_target_probe_runs(db, target_id)
        assert [r.run_id for r in rows] == [latest, mid, earlier, running]

    def test_excludes_other_modules_and_targets(self, db: sqlite3.Connection) -> None:
        t1 = create_target(db, type="server", name="probe-t1")
        t2 = create_target(db, type="server", name="probe-t2")
        own = _completed_run(
            db,
            module="ipi-probe",
            target_id=t1,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        _completed_run(
            db,
            module="ipi-probe",
            target_id=t2,
            finished_at="2026-04-11T12:00:00+00:00",
        )
        _completed_run(
            db,
            module="ipi-sweep",
            target_id=t1,
            finished_at="2026-04-12T12:00:00+00:00",
        )
        _completed_run(
            db,
            module="import",
            target_id=t1,
            finished_at="2026-04-13T12:00:00+00:00",
        )

        rows = run_service.query_target_probe_runs(db, t1)
        assert [r.run_id for r in rows] == [own]

    def test_row_without_metadata_renders_with_flag(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="no-metadata-probe")
        run_id = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        rows = run_service.query_target_probe_runs(db, target_id)
        assert len(rows) == 1
        assert rows[0].run_id == run_id
        assert rows[0].metadata_available is False
        assert rows[0].total_probes == 0
        assert rows[0].category_count == 0

    def test_mixed_tz_naive_and_aware_does_not_raise(self, db: sqlite3.Connection) -> None:
        target_id = create_target(db, type="server", name="mixed-tz-probe")
        aware = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-10T12:00:00+00:00",
        )
        naive = _completed_run(
            db,
            module="ipi-probe",
            target_id=target_id,
            finished_at="2026-04-11T12:00:00",
        )
        rows = run_service.query_target_probe_runs(db, target_id)
        assert {r.run_id for r in rows} == {aware, naive}
