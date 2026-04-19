"""Template auto-selection from IPI sweep findings.

Given a target ID, ``select_template_for_target`` reads the most recent
completed ``ipi-sweep`` run for that target, reduces per-(template, style)
compliance rates to a per-template max, and returns either a chosen
template or a structured refusal reason (tie, stale-refuse, no-findings).

Thresholds are hardcoded v1 defaults — see the IPI sweep integration
design RFC. They may tighten as sweep sample sizes grow.

Numerical choices:

* **Tie comparison** is on integer percentage points (``round(rate*100)``).
  Comparing raw floats would flip tie behavior under fixed data at 10pp
  boundaries: ``0.4 - 0.3`` evaluates to ``0.10000000000000003`` in IEEE
  754, while ``0.2 - 0.1`` evaluates to exactly ``0.1``. Rounding to
  integer percent before subtracting makes the rule stable across the
  rate axis.
* **Display rounding** for the one-line prefix uses ``round()`` (banker's
  rounding, ties-to-even) on the raw float.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from q_ai.core.db import get_connection, list_evidence, list_runs
from q_ai.core.models import Run, RunStatus
from q_ai.ipi.models import DocumentTemplate

logger = logging.getLogger(__name__)

SWEEP_MODULE = "ipi-sweep"
"""Module filter for sweep runs (matches :func:`persist_sweep_run`)."""

METADATA_EVIDENCE_TYPE = "ipi_sweep_metadata"
"""Evidence type carrying the structured ``combination_summary`` blob."""

TIE_BAND_PP = 10
"""Inclusive tie-band width, in integer percentage points."""

STALE_WARN_DAYS = 7
"""Age (in days) at which a stale-warn flag is set on a successful select."""

STALE_REFUSE_DAYS = 30
"""Age (in days) past which the most recent run is rejected as stale."""


# ---------------------------------------------------------------------------
# Result variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectedTemplate:
    """Auto-select succeeded; ``template`` should drive generation.

    Attributes:
        template: Winning template.
        run_id: Source sweep run ID.
        completed_at: Source run's finished_at (UTC-aware).
        compliance_rate: Raw float compliance rate (0.0-1.0) for the
            winner's best style.
        age_days: Integer days between ``completed_at`` and ``now``.
        stale_warn: True when the chosen run is between ``STALE_WARN_DAYS``
            (exclusive) and ``STALE_REFUSE_DAYS`` (inclusive) old.
    """

    template: DocumentTemplate
    run_id: str
    completed_at: datetime.datetime
    compliance_rate: float
    age_days: int
    stale_warn: bool


@dataclass(frozen=True)
class TieRefusal:
    """Two or more templates are within ``TIE_BAND_PP`` of the top.

    Attributes:
        candidates: Every (template, compliance_rate) pair inside the
            inclusive 10pp band, ordered by rate descending.
        run_id: Source sweep run ID.
    """

    candidates: list[tuple[DocumentTemplate, float]]
    run_id: str


@dataclass(frozen=True)
class StaleRefusal:
    """Most recent sweep run is older than ``STALE_REFUSE_DAYS``.

    Attributes:
        run_id: Source sweep run ID.
        completed_at: The run's finished_at (UTC-aware).
        age_days: Integer days between ``completed_at`` and ``now``.
    """

    run_id: str
    completed_at: datetime.datetime
    age_days: int


@dataclass(frozen=True)
class NoFindings:
    """Target has no completed sweep runs (or run contained no findings).

    Attributes:
        target_id: The target that was queried.
    """

    target_id: str


SelectionResult = SelectedTemplate | TieRefusal | StaleRefusal | NoFindings
"""Discriminated union returned by :func:`select_template_for_target`."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def select_template_for_target(
    target_id: str,
    *,
    now: datetime.datetime | None = None,
    db_path: Path | None = None,
) -> SelectionResult:
    """Select the best IPI template for a target from its sweep findings.

    Queries the most recent completed ``ipi-sweep`` run for ``target_id``
    and applies the selection contract from the IPI sweep integration
    design RFC: max-across-styles per template, inclusive 10pp tie band,
    7d stale-warn, 30d stale-refuse.

    Args:
        target_id: Target ID previously associated with a sweep run.
        now: UTC-aware timestamp used for age computation. Defaults to
            ``datetime.now(UTC)``. Injected for deterministic tests.
        db_path: Override database path (for testing).

    Returns:
        One of :class:`SelectedTemplate`, :class:`TieRefusal`,
        :class:`StaleRefusal`, :class:`NoFindings`.
    """
    reference_now = _resolve_now(now)

    with get_connection(db_path) as conn:
        run = _most_recent_completed_run(conn, target_id)
        if run is None or run.finished_at is None:
            return NoFindings(target_id=target_id)

        completed_at = _as_utc(run.finished_at)
        age_days = _age_in_days(completed_at, reference_now)

        if age_days > STALE_REFUSE_DAYS:
            return StaleRefusal(
                run_id=run.id,
                completed_at=completed_at,
                age_days=age_days,
            )

        per_template = _max_rate_per_template(conn, run.id)

    if not per_template:
        return NoFindings(target_id=target_id)

    ranked = sorted(per_template.items(), key=lambda kv: kv[1], reverse=True)
    stale_warn = age_days > STALE_WARN_DAYS

    tie = _detect_tie(ranked, run.id)
    if tie is not None:
        return tie

    winner_template, winner_rate = ranked[0]
    return SelectedTemplate(
        template=winner_template,
        run_id=run.id,
        completed_at=completed_at,
        compliance_rate=winner_rate,
        age_days=age_days,
        stale_warn=stale_warn,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_now(now: datetime.datetime | None) -> datetime.datetime:
    """Return a UTC-aware ``now`` (default: current UTC time).

    Args:
        now: Optional caller-supplied timestamp.

    Returns:
        UTC-aware datetime.
    """
    if now is None:
        return datetime.datetime.now(datetime.UTC)
    return _as_utc(now)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    """Coerce a datetime to UTC-aware.

    Naive datetimes are assumed to already be UTC (matches how
    :func:`q_ai.core.db._now_iso` writes timestamps).

    Args:
        value: Datetime to coerce.

    Returns:
        UTC-aware datetime.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _age_in_days(completed_at: datetime.datetime, now: datetime.datetime) -> int:
    """Compute integer age in days using floor division on the timedelta.

    A 7.9-day-old run returns 7 (warns); a 30.5-day-old run returns 30
    (still allowed); 31 is the first day that refuses.

    Args:
        completed_at: UTC-aware timestamp of the sweep run's completion.
        now: UTC-aware reference timestamp.

    Returns:
        Non-negative integer days.
    """
    delta = now - completed_at
    if delta.total_seconds() < 0:
        return 0
    return delta.days


def _most_recent_completed_run(conn: sqlite3.Connection, target_id: str) -> Run | None:
    """Return the newest completed ipi-sweep run for a target, or None.

    Prefers ``finished_at`` as the ordering key (brief's staleness clock
    source) with ``started_at`` used as a deterministic tiebreaker for
    the rare case where two runs share the same finish timestamp.

    Args:
        conn: Active database connection.
        target_id: Target ID to query.

    Returns:
        Most recent completed :class:`Run`, or None if none exist.
    """
    runs = list_runs(
        conn,
        module=SWEEP_MODULE,
        status=RunStatus.COMPLETED,
        target_id=target_id,
    )
    eligible = [r for r in runs if r.finished_at is not None]
    if not eligible:
        return None
    eligible.sort(key=lambda r: (r.finished_at, r.started_at), reverse=True)
    return eligible[0]


def _max_rate_per_template(conn: sqlite3.Connection, run_id: str) -> dict[DocumentTemplate, float]:
    """Reduce a sweep run's combination_summary to max rate per template.

    Reads the ``ipi_sweep_metadata`` evidence blob (the only structured
    carrier for raw compliance rates; finding rows keep only a formatted
    title/description string).

    Args:
        conn: Active database connection.
        run_id: Sweep run ID.

    Returns:
        Insertion-ordered mapping from template enum to max compliance
        rate across its styles. Empty if the blob is missing or holds no
        usable entries.
    """
    summary = _load_combination_summary(conn, run_id)
    if not summary:
        return {}

    per_template: dict[DocumentTemplate, float] = {}
    template_by_value = {t.value: t for t in DocumentTemplate}

    for entry in summary:
        if not isinstance(entry, dict):
            continue
        template_value = entry.get("template")
        rate = entry.get("rate")
        template = (
            template_by_value.get(template_value) if isinstance(template_value, str) else None
        )
        if template is None:
            continue
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            continue
        rate_float = float(rate)
        existing = per_template.get(template)
        if existing is None or rate_float > existing:
            per_template[template] = rate_float

    return per_template


def _load_combination_summary(conn: sqlite3.Connection, run_id: str) -> list[dict] | None:
    """Load and JSON-decode the metadata evidence's combination_summary.

    Args:
        conn: Active database connection.
        run_id: Sweep run ID.

    Returns:
        The decoded combination_summary list, or None if missing or
        malformed.
    """
    records = list_evidence(conn, run_id=run_id)
    metadata = next((e for e in records if e.type == METADATA_EVIDENCE_TYPE), None)
    if metadata is None or not metadata.content:
        return None

    try:
        parsed = json.loads(metadata.content)
    except json.JSONDecodeError:
        logger.warning("Sweep metadata evidence for run %s is not valid JSON", run_id)
        return None

    if not isinstance(parsed, dict):
        return None
    summary = parsed.get("combination_summary")
    if not isinstance(summary, list):
        return None
    return summary


def _detect_tie(
    ranked: list[tuple[DocumentTemplate, float]],
    run_id: str,
) -> TieRefusal | None:
    """Return a TieRefusal if ≥2 templates sit inside the 10pp band.

    Uses integer-percentage-point comparison (rounded from raw floats)
    to avoid IEEE 754 boundary flips. The returned candidate list
    includes every template inside the inclusive band, not just the top
    two — researchers should see all options during a near-tie.

    Args:
        ranked: Templates ordered by compliance rate, descending.
        run_id: Sweep run ID (surfaced in the refusal).

    Returns:
        :class:`TieRefusal` or None.
    """
    if len(ranked) < 2:
        return None

    top_pp = _as_pp(ranked[0][1])
    second_pp = _as_pp(ranked[1][1])
    if top_pp - second_pp > TIE_BAND_PP:
        return None

    candidates = [(t, r) for t, r in ranked if top_pp - _as_pp(r) <= TIE_BAND_PP]
    return TieRefusal(candidates=candidates, run_id=run_id)


def _as_pp(rate: float) -> int:
    """Round a 0.0-1.0 rate to an integer percentage point.

    Args:
        rate: Compliance rate.

    Returns:
        Integer percentage (banker's rounding via ``round``).
    """
    return round(rate * 100)
