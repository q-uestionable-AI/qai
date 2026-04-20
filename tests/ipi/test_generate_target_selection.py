"""Unit tests for :mod:`q_ai.ipi.sweep_selection`."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    get_connection,
    update_run_status,
)
from q_ai.core.models import RunStatus, Severity
from q_ai.ipi.models import DocumentTemplate, PayloadStyle
from q_ai.ipi.sweep_selection import (
    METADATA_EVIDENCE_TYPE,
    SWEEP_MODULE,
    NoFindings,
    SelectedTemplate,
    StaleRefusal,
    TieRefusal,
    select_template_for_target,
)

_FIXED_NOW = datetime.datetime(2026, 4, 18, 12, 0, 0, tzinfo=datetime.UTC)


def _ensure_target(conn, target_id: str) -> None:
    """Insert a minimal target row if one with ``target_id`` does not exist.

    The runs table has a FK to targets; tests use stable IDs like "t1"
    rather than generated UUIDs, so the row must be created manually.

    Args:
        conn: Active database connection.
        target_id: Target ID to upsert.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO targets (id, type, name, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (target_id, "test", f"target-{target_id}", _FIXED_NOW.isoformat()),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_run(
    db_path: Path,
    *,
    target_id: str,
    finished_at: datetime.datetime | None,
    combinations: list[tuple[DocumentTemplate, PayloadStyle, float]],
    status: RunStatus = RunStatus.COMPLETED,
    run_id: str | None = None,
    citation_frame: str | None = "template-aware",
) -> str:
    """Persist a synthetic sweep run with the given finish time + rates.

    Writes one finding per combination and a metadata evidence blob whose
    ``combination_summary`` carries the raw rates that selection reads.

    Args:
        db_path: Target database path.
        target_id: Target association for the run.
        finished_at: Desired UTC-aware finish timestamp. Pass None to
            leave the run with ``finished_at = NULL`` (unusual — emulates
            a partial write).
        combinations: Tuples of (template, style, rate) with rate in
            [0.0, 1.0].
        status: Run status to persist (defaults to COMPLETED).
        run_id: Optional deterministic run ID.
        citation_frame: Value to write into the metadata blob's
            ``citation_frame`` field. Pass ``None`` to omit the field
            entirely (simulates pre-v0.10.2 data). Defaults to
            ``"template-aware"`` so tests that pre-date the frame
            filter continue to produce eligible runs.

    Returns:
        The persisted run ID.
    """
    with get_connection(db_path) as conn:
        _ensure_target(conn, target_id)
        created_id = create_run(
            conn,
            module=SWEEP_MODULE,
            name="test-sweep",
            target_id=target_id,
            source="test",
            run_id=run_id,
        )
        for template, style, rate in combinations:
            create_finding(
                conn,
                run_id=created_id,
                module=SWEEP_MODULE,
                category=template.value,
                severity=Severity.INFO,
                title=f"IPI Sweep: {template.value} / {style.value} — {rate:.0%}",
                description="seeded",
                framework_ids={"ipi_sweep": f"{template.value}/{style.value}"},
                source_ref=f"ipi-sweep/{template.value}/{style.value}",
            )
        metadata: dict[str, object] = {
            "combination_summary": [
                {
                    "template": t.value,
                    "style": s.value,
                    "total": 10,
                    "complied": round(r * 10),
                    "rate": r,
                    "severity": "INFO",
                }
                for t, s, r in combinations
            ],
        }
        if citation_frame is not None:
            metadata["citation_frame"] = citation_frame
        create_evidence(
            conn,
            type=METADATA_EVIDENCE_TYPE,
            run_id=created_id,
            storage="inline",
            content=json.dumps(metadata),
        )
        iso = finished_at.isoformat() if finished_at is not None else None
        update_run_status(conn, created_id, status, finished_at=iso)
    return created_id


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Per-test sqlite path."""
    return tmp_path / "test.db"


def _completed_at(days_ago: int) -> datetime.datetime:
    """UTC-aware timestamp exactly ``days_ago`` days before ``_FIXED_NOW``."""
    return _FIXED_NOW - datetime.timedelta(days=days_ago)


# ---------------------------------------------------------------------------
# Basic selection paths
# ---------------------------------------------------------------------------


class TestNoFindings:
    """Behavior when the target has no usable sweep findings."""

    def test_no_runs_at_all_returns_no_findings(self, db_path: Path) -> None:
        result = select_template_for_target(
            "target-without-runs",
            now=_FIXED_NOW,
            db_path=db_path,
        )
        assert isinstance(result, NoFindings)
        assert result.target_id == "target-without-runs"


class TestClearWinner:
    """Single-winner selection."""

    def test_single_clear_winner(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.40),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.template == DocumentTemplate.WHOIS
        assert result.compliance_rate == pytest.approx(0.80)
        assert result.age_days == 1
        assert result.stale_warn is False


# ---------------------------------------------------------------------------
# Tie-band behavior — includes the IEEE 754 boundary test
# ---------------------------------------------------------------------------


class TestTieBand:
    """Inclusive 10pp tie-band detection on integer percentage points."""

    def test_two_templates_within_10pp_is_tie(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.75),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.70),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, TieRefusal)
        tied_templates = {t for t, _ in result.candidates}
        assert tied_templates == {DocumentTemplate.WHOIS, DocumentTemplate.REPORT}

    def test_float_subtraction_artifact_still_flagged(self, db_path: Path) -> None:
        """0.4 and 0.3 must tie under the 10pp rule (IEEE 754 guard)."""
        assert 0.4 - 0.3 != 0.1  # sanity: confirms the artifact exists
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.40),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.30),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, TieRefusal)
        tied_templates = {t for t, _ in result.candidates}
        assert tied_templates == {DocumentTemplate.WHOIS, DocumentTemplate.REPORT}

    def test_float_artifact_high_scale_still_flagged(self, db_path: Path) -> None:
        """0.8 and 0.7 must tie — further IEEE 754 coverage on the scale."""
        assert 0.8 - 0.7 != 0.1
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.70),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, TieRefusal)
        tied_templates = {t for t, _ in result.candidates}
        assert tied_templates == {DocumentTemplate.WHOIS, DocumentTemplate.REPORT}

    def test_three_templates_only_band_members_listed(self, db_path: Path) -> None:
        """Third template outside 10pp is excluded from the tied list."""
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.75),
                (DocumentTemplate.EMAIL, PayloadStyle.OBVIOUS, 0.40),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, TieRefusal)
        tied_templates = {t for t, _ in result.candidates}
        assert tied_templates == {DocumentTemplate.WHOIS, DocumentTemplate.REPORT}
        assert DocumentTemplate.EMAIL not in tied_templates

    def test_gap_greater_than_10pp_is_not_tie(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.69),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.template == DocumentTemplate.WHOIS


# ---------------------------------------------------------------------------
# Max-across-styles reduction
# ---------------------------------------------------------------------------


class TestMaxAcrossStyles:
    """Per-template reduction takes the max rate across styles."""

    def test_same_template_multiple_styles_no_false_tie(self, db_path: Path) -> None:
        """A template winning across styles collapses into one entry."""
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.WHOIS, PayloadStyle.CITATION, 0.70),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.40),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.template == DocumentTemplate.WHOIS
        # The WHOIS entry's max across styles is 0.80, not 0.70.
        assert result.compliance_rate == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# Run selection (most-recent-completed, status filter)
# ---------------------------------------------------------------------------


class TestRunSelection:
    """Selection picks the most recent completed run."""

    def test_prefers_most_recent_completed_run(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(10),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.90),
            ],
            run_id="older-run",
        )
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.60),
            ],
            run_id="newer-run",
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.template == DocumentTemplate.REPORT
        assert result.run_id == "newer-run"

    def test_running_newer_run_ignored_for_older_completed(self, db_path: Path) -> None:
        """A newer run in RUNNING status is skipped by the status filter."""
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(5),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.90),
            ],
            run_id="completed-older",
        )
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=None,
            combinations=[
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.60),
            ],
            status=RunStatus.RUNNING,
            run_id="running-newer",
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.run_id == "completed-older"
        assert result.template == DocumentTemplate.WHOIS


# ---------------------------------------------------------------------------
# Staleness thresholds (7 warn, 30 refuse)
# ---------------------------------------------------------------------------


class TestStaleness:
    """Age-gate behavior at the 7d warn and 30d refuse boundaries."""

    def test_age_eight_days_sets_stale_warn(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(8),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.stale_warn is True
        assert result.age_days == 8

    def test_age_seven_days_no_stale_warn(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(7),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.stale_warn is False

    def test_age_thirty_days_still_allowed(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(30),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.stale_warn is True
        assert result.age_days == 30

    def test_age_thirty_one_days_refused(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(31),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
            ],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, StaleRefusal)
        assert result.age_days == 31


# ---------------------------------------------------------------------------
# Degenerate runs
# ---------------------------------------------------------------------------


class TestDegenerateRuns:
    """Edge cases that collapse to no-findings."""

    def test_completed_run_with_no_combinations(self, db_path: Path) -> None:
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[],
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, NoFindings)


# ---------------------------------------------------------------------------
# citation_frame filter (v0.10.2)
# ---------------------------------------------------------------------------


class TestCitationFrameFilter:
    """Auto-select considers only template-aware sweep runs.

    Plain-frame runs are control-condition measurements for Campaign 1
    Phase 4 Step 3. Allowing them to drive ``qai ipi generate --target``
    would conflate baseline framing rates with production framing rates
    — exactly the selection bug PR #134 introduced and this brief closes.
    """

    def test_single_template_aware_run_selects(self, db_path: Path) -> None:
        """One template-aware run with a clear winner yields SelectedTemplate.

        Regression guard: the filter must not exclude default-frame runs.
        """
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.20),
            ],
            citation_frame="template-aware",
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.template == DocumentTemplate.WHOIS

    def test_newer_plain_run_is_skipped_for_older_template_aware(self, db_path: Path) -> None:
        """Mixed history: the older template-aware run wins over the newer plain.

        The plain run has higher compliance (would otherwise win on
        recency + rate), proving the filter fires before ranking.
        """
        older_id = _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(5),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.60),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.10),
            ],
            citation_frame="template-aware",
            run_id="older-template-aware",
        )
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.95),
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.05),
            ],
            citation_frame="plain",
            run_id="newer-plain",
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.run_id == older_id
        assert result.template == DocumentTemplate.WHOIS

    def test_plain_only_history_returns_no_findings(self, db_path: Path) -> None:
        """A target with only plain-frame runs returns NoFindings, not a selection."""
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.20),
            ],
            citation_frame="plain",
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, NoFindings)

    def test_legacy_run_with_no_frame_treated_as_template_aware(self, db_path: Path) -> None:
        """Pre-v0.10.2 runs (no ``citation_frame`` key) remain eligible.

        Passing ``citation_frame=None`` to the fixture omits the key
        from the metadata blob entirely, simulating a run persisted
        before the field existed.
        """
        _seed_run(
            db_path,
            target_id="t1",
            finished_at=_completed_at(1),
            combinations=[
                (DocumentTemplate.WHOIS, PayloadStyle.OBVIOUS, 0.80),
                (DocumentTemplate.REPORT, PayloadStyle.OBVIOUS, 0.20),
            ],
            citation_frame=None,
        )
        result = select_template_for_target("t1", now=_FIXED_NOW, db_path=db_path)
        assert isinstance(result, SelectedTemplate)
        assert result.template == DocumentTemplate.WHOIS
