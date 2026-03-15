"""Tests for the inject adapter."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.inject.adapter import InjectAdapter, InjectResult
from q_ai.inject.models import Campaign, InjectionOutcome, InjectionResult
from q_ai.orchestrator.runner import WorkflowRunner


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a temporary database with schema applied."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def runner(db_path: Path) -> WorkflowRunner:
    """Create a WorkflowRunner with a temp database."""
    return WorkflowRunner(
        workflow_id="assess",
        config={},
        db_path=db_path,
    )


def _make_injection_result(
    outcome: InjectionOutcome = InjectionOutcome.FULL_COMPLIANCE,
    payload_name: str = "test_payload",
) -> InjectionResult:
    return InjectionResult(
        payload_name=payload_name,
        technique="description_poisoning",
        outcome=outcome,
        evidence='[{"type": "tool_use"}]',
        target_agent="test-model",
        timestamp=datetime(2026, 3, 3, tzinfo=UTC),
    )


def _make_campaign(results: list[InjectionResult] | None = None) -> Campaign:
    return Campaign(
        id="campaign-test",
        name="test-campaign",
        model="test-model",
        results=results or [],
        started_at=datetime(2026, 3, 3, tzinfo=UTC),
        finished_at=datetime(2026, 3, 3, 0, 1, tzinfo=UTC),
    )


class TestInjectAdapter:
    """Tests for InjectAdapter orchestration glue."""

    async def test_run_creates_child_run(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify child run created with module='inject' and parent_run_id set."""
        await runner.start()

        campaign = _make_campaign([_make_injection_result()])

        adapter = InjectAdapter(runner, {"model": "test-model"})

        with (
            patch("q_ai.inject.adapter.load_all_templates", return_value=[]),
            patch("q_ai.inject.adapter.run_campaign", return_value=campaign),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.module == "inject"
        assert child.parent_run_id == runner.run_id

    async def test_run_persists_campaign(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify inject_results rows created with correct run_id."""
        await runner.start()

        results = [
            _make_injection_result(InjectionOutcome.FULL_COMPLIANCE, "p1"),
            _make_injection_result(InjectionOutcome.CLEAN_REFUSAL, "p2"),
        ]
        campaign = _make_campaign(results)

        adapter = InjectAdapter(runner, {"model": "test-model"})

        with (
            patch("q_ai.inject.adapter.load_all_templates", return_value=[]),
            patch("q_ai.inject.adapter.run_campaign", return_value=campaign),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            inject_results = conn.execute(
                "SELECT * FROM inject_results WHERE run_id = ?", (result.run_id,)
            ).fetchall()
            assert len(inject_results) == 2

    async def test_run_emits_finding_events(self, db_path: Path) -> None:
        """Verify finding events emitted for security-relevant outcomes."""
        ws_manager = AsyncMock()
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            ws_manager=ws_manager,
            db_path=db_path,
        )
        await runner.start()

        results = [
            _make_injection_result(InjectionOutcome.FULL_COMPLIANCE, "p1"),
            _make_injection_result(InjectionOutcome.PARTIAL_COMPLIANCE, "p2"),
            _make_injection_result(InjectionOutcome.CLEAN_REFUSAL, "p3"),
        ]
        campaign = _make_campaign(results)

        adapter = InjectAdapter(runner, {"model": "test-model"})

        with (
            patch("q_ai.inject.adapter.load_all_templates", return_value=[]),
            patch("q_ai.inject.adapter.run_campaign", return_value=campaign),
        ):
            await adapter.run()

        finding_calls = [
            call
            for call in ws_manager.broadcast.call_args_list
            if call.args[0].get("type") == "finding"
        ]
        # FULL_COMPLIANCE and PARTIAL_COMPLIANCE create findings, CLEAN_REFUSAL does not
        assert len(finding_calls) == 2

    async def test_run_sets_completed_on_success(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Verify child run status=COMPLETED after run()."""
        await runner.start()

        campaign = _make_campaign()
        adapter = InjectAdapter(runner, {"model": "test-model"})

        with (
            patch("q_ai.inject.adapter.load_all_templates", return_value=[]),
            patch("q_ai.inject.adapter.run_campaign", return_value=campaign),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_run_sets_failed_on_error(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Mock run_campaign to raise, verify status=FAILED."""
        await runner.start()

        adapter = InjectAdapter(runner, {"model": "test-model"})

        with (
            patch("q_ai.inject.adapter.load_all_templates", return_value=[]),
            patch(
                "q_ai.inject.adapter.run_campaign",
                side_effect=RuntimeError("API error"),
            ),
            pytest.raises(RuntimeError, match="API error"),
        ):
            await adapter.run()

        with get_connection(db_path) as conn:
            children = conn.execute(
                "SELECT id FROM runs WHERE parent_run_id = ?", (runner.run_id,)
            ).fetchall()
            assert len(children) == 1
            child = get_run(conn, children[0]["id"])
        assert child is not None
        assert child.status == RunStatus.FAILED

    async def test_run_returns_inject_result(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify InjectResult fields."""
        await runner.start()

        results = [
            _make_injection_result(InjectionOutcome.FULL_COMPLIANCE, "p1"),
            _make_injection_result(InjectionOutcome.REFUSAL_WITH_LEAK, "p2"),
            _make_injection_result(InjectionOutcome.CLEAN_REFUSAL, "p3"),
        ]
        campaign = _make_campaign(results)
        adapter = InjectAdapter(runner, {"model": "test-model"})

        with (
            patch("q_ai.inject.adapter.load_all_templates", return_value=[]),
            patch("q_ai.inject.adapter.run_campaign", return_value=campaign),
        ):
            result = await adapter.run()

        assert isinstance(result, InjectResult)
        assert result.run_id
        assert result.campaign is campaign
        assert result.finding_count == 2  # FULL_COMPLIANCE + REFUSAL_WITH_LEAK
