"""Tests for chain mapper — persist_chain writes to DB correctly."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from q_ai.chain.executor_models import StepOutput
from q_ai.chain.mapper import persist_chain
from q_ai.chain.models import (
    ChainCategory,
    ChainDefinition,
    ChainResult,
    StepStatus,
)
from q_ai.core.db import get_connection


def _make_chain_def() -> ChainDefinition:
    """Build a simple ChainDefinition for testing."""
    return ChainDefinition(
        id="test-chain",
        name="Test Chain",
        category=ChainCategory.RAG_PIPELINE,
        description="Test chain for mapper tests",
    )


def _make_result(dry_run: bool = False) -> ChainResult:
    """Build a ChainResult with step outputs for testing."""
    now = datetime.now(UTC)
    return ChainResult(
        chain_id="test-chain",
        chain_name="Test Chain",
        target_config={"audit_transport": "stdio"},
        step_outputs=[
            StepOutput(
                step_id="step-1",
                module="audit",
                technique="injection",
                success=True,
                status=StepStatus.SUCCESS,
                artifacts={"vulnerable_tool": "exec_cmd", "finding_count": "3"},
                started_at=now,
                finished_at=now,
            ),
            StepOutput(
                step_id="step-2",
                module="inject",
                technique="description_poisoning",
                success=False,
                status=StepStatus.FAILED,
                error="Campaign failed",
                started_at=now,
                finished_at=now,
            ),
        ],
        trust_boundaries_crossed=["client-to-server", "agent-to-tool"],
        started_at=now,
        finished_at=now,
        dry_run=dry_run,
    )


class TestPersistChain:
    """Tests for persist_chain."""

    def test_creates_run_record(self, tmp_path: Path) -> None:
        """persist_chain creates a run record with module='chain'."""
        db_path = tmp_path / "test.db"
        result = _make_result()
        chain_def = _make_chain_def()

        run_id = persist_chain(result, chain_def, db_path=db_path)

        assert run_id
        with get_connection(db_path) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        assert row["module"] == "chain"
        assert row["name"] == "Test Chain"

    def test_creates_chain_execution(self, tmp_path: Path) -> None:
        """persist_chain creates a chain_executions row."""
        db_path = tmp_path / "test.db"
        result = _make_result()
        chain_def = _make_chain_def()

        run_id = persist_chain(result, chain_def, db_path=db_path)

        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM chain_executions WHERE run_id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["chain_id"] == "test-chain"
        assert row["chain_name"] == "Test Chain"
        assert row["dry_run"] == 0
        assert row["success"] == 0  # last step failed
        boundaries = json.loads(row["trust_boundaries"])
        assert boundaries == ["client-to-server", "agent-to-tool"]

    def test_creates_step_outputs(self, tmp_path: Path) -> None:
        """persist_chain creates chain_step_outputs rows."""
        db_path = tmp_path / "test.db"
        result = _make_result()
        chain_def = _make_chain_def()

        run_id = persist_chain(result, chain_def, db_path=db_path)

        with get_connection(db_path) as conn:
            exec_row = conn.execute(
                "SELECT id FROM chain_executions WHERE run_id = ?", (run_id,)
            ).fetchone()
            step_rows = conn.execute(
                "SELECT * FROM chain_step_outputs WHERE execution_id = ? ORDER BY step_id",
                (exec_row["id"],),
            ).fetchall()

        assert len(step_rows) == 2
        step1 = step_rows[0]
        assert step1["step_id"] == "step-1"
        assert step1["module"] == "audit"
        assert step1["technique"] == "injection"
        assert step1["success"] == 1
        artifacts = json.loads(step1["artifacts"])
        assert artifacts["vulnerable_tool"] == "exec_cmd"

        step2 = step_rows[1]
        assert step2["step_id"] == "step-2"
        assert step2["success"] == 0
        assert step2["error"] == "Campaign failed"

    def test_dry_run_flag(self, tmp_path: Path) -> None:
        """persist_chain records dry_run=1 for dry-run executions."""
        db_path = tmp_path / "test.db"
        result = _make_result(dry_run=True)
        chain_def = _make_chain_def()

        run_id = persist_chain(result, chain_def, db_path=db_path)

        with get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT dry_run FROM chain_executions WHERE run_id = ?", (run_id,)
            ).fetchone()
        assert row["dry_run"] == 1

    def test_empty_step_outputs(self, tmp_path: Path) -> None:
        """persist_chain handles empty step_outputs."""
        db_path = tmp_path / "test.db"
        result = ChainResult(chain_id="empty", chain_name="Empty")
        chain_def = _make_chain_def()

        run_id = persist_chain(result, chain_def, db_path=db_path)

        with get_connection(db_path) as conn:
            exec_row = conn.execute(
                "SELECT id FROM chain_executions WHERE run_id = ?", (run_id,)
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM chain_step_outputs WHERE execution_id = ?",
                (exec_row["id"],),
            ).fetchone()
        assert count["cnt"] == 0
