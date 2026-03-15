"""Tests for inject campaign DB persistence mapper."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from q_ai.core.db import create_run, get_connection
from q_ai.core.models import Severity
from q_ai.inject.mapper import persist_campaign
from q_ai.inject.models import Campaign, InjectionOutcome, InjectionResult


def _make_campaign(results: list[InjectionResult] | None = None) -> Campaign:
    return Campaign(
        id="campaign-test",
        name="test-campaign",
        model="test-model",
        results=results or [],
        started_at=datetime(2026, 3, 3, tzinfo=UTC),
        finished_at=datetime(2026, 3, 3, 0, 1, tzinfo=UTC),
    )


def _make_result(
    outcome: InjectionOutcome = InjectionOutcome.FULL_COMPLIANCE,
    payload_name: str = "test_payload",
    technique: str = "description_poisoning",
) -> InjectionResult:
    return InjectionResult(
        payload_name=payload_name,
        technique=technique,
        outcome=outcome,
        evidence='[{"type": "tool_use"}]',
        target_agent="test-model",
        timestamp=datetime(2026, 3, 3, tzinfo=UTC),
    )


class TestPersistCampaign:
    def test_empty_campaign_creates_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign()
        run_id = persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert row is not None
            assert conn.execute("SELECT COUNT(*) FROM inject_results").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0

    def test_full_compliance_creates_finding(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign([_make_result(InjectionOutcome.FULL_COMPLIANCE)])
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM inject_results").fetchone()[0] == 1
            finding = conn.execute("SELECT * FROM findings").fetchone()
            assert finding is not None
            assert finding["severity"] == int(Severity.CRITICAL)

    def test_partial_compliance_creates_high_finding(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign([_make_result(InjectionOutcome.PARTIAL_COMPLIANCE)])
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            finding = conn.execute("SELECT * FROM findings").fetchone()
            assert finding["severity"] == int(Severity.HIGH)

    def test_refusal_with_leak_creates_medium_finding(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign([_make_result(InjectionOutcome.REFUSAL_WITH_LEAK)])
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            finding = conn.execute("SELECT * FROM findings").fetchone()
            assert finding["severity"] == int(Severity.MEDIUM)

    def test_clean_refusal_no_finding(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign([_make_result(InjectionOutcome.CLEAN_REFUSAL)])
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM inject_results").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0

    def test_error_no_finding(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign([_make_result(InjectionOutcome.ERROR)])
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM inject_results").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0

    def test_mixed_outcomes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        results = [
            _make_result(InjectionOutcome.FULL_COMPLIANCE, payload_name="p1"),
            _make_result(InjectionOutcome.PARTIAL_COMPLIANCE, payload_name="p2"),
            _make_result(InjectionOutcome.REFUSAL_WITH_LEAK, payload_name="p3"),
            _make_result(InjectionOutcome.CLEAN_REFUSAL, payload_name="p4"),
            _make_result(InjectionOutcome.ERROR, payload_name="p5"),
        ]
        campaign = _make_campaign(results)
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM inject_results").fetchone()[0] == 5
            assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 3

    def test_finding_fields(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        campaign = _make_campaign([_make_result()])
        persist_campaign(campaign, db_path=db_path)
        with get_connection(db_path) as conn:
            finding = conn.execute("SELECT * FROM findings").fetchone()
            assert finding["module"] == "inject"
            assert finding["category"] == "description_poisoning"
            assert "test_payload" in finding["title"]

    def test_persist_with_explicit_run_id(self, tmp_path: Path) -> None:
        """Verify persist_campaign uses provided run_id instead of creating a new one."""
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            pre_run_id = create_run(conn, module="inject", name="pre-created")

        campaign = _make_campaign([_make_result()])
        run_id = persist_campaign(campaign, db_path=db_path, run_id=pre_run_id)
        assert run_id == pre_run_id

        with get_connection(db_path) as conn:
            # Should only have the pre-created run
            runs = conn.execute("SELECT * FROM runs").fetchall()
            assert len(runs) == 1
            assert runs[0]["id"] == pre_run_id

            # Results should reference the pre-created run
            results = conn.execute(
                "SELECT * FROM inject_results WHERE run_id = ?", (pre_run_id,)
            ).fetchall()
            assert len(results) == 1
