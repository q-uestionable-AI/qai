"""Tests for the chain adapter."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from q_ai.chain.adapter import ChainAdapter, ChainAdapterResult
from q_ai.chain.loader import ChainValidationError
from q_ai.chain.models import (
    ChainCategory,
    ChainDefinition,
    ChainResult,
    ChainStep,
    StepStatus,
)
from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
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
        workflow_id="test-workflow",
        config={},
        db_path=db_path,
    )


def _make_chain_def() -> ChainDefinition:
    """Build a simple ChainDefinition for testing."""
    return ChainDefinition(
        id="test-chain",
        name="Test Chain",
        category=ChainCategory.RAG_PIPELINE,
        description="Test chain",
        steps=[
            ChainStep(
                id="step-1",
                name="Audit step",
                module="audit",
                technique="injection",
            ),
            ChainStep(
                id="step-2",
                name="Inject step",
                module="inject",
                technique="description_poisoning",
                terminal=True,
            ),
        ],
    )


def _make_chain_result(success: bool = True) -> ChainResult:
    """Build a ChainResult with optional success flag."""
    now = datetime.now(UTC)

    @dataclass
    class FakeStepOutput:
        step_id: str
        module: str
        technique: str
        success: bool
        status: StepStatus
        artifacts: dict = field(default_factory=dict)
        started_at: datetime = field(default_factory=lambda: now)
        finished_at: datetime | None = None
        error: str | None = None

    outputs = [
        FakeStepOutput(
            step_id="step-1",
            module="audit",
            technique="injection",
            success=True,
            status=StepStatus.SUCCESS,
        ),
        FakeStepOutput(
            step_id="step-2",
            module="inject",
            technique="description_poisoning",
            success=success,
            status=StepStatus.SUCCESS if success else StepStatus.FAILED,
            error=None if success else "Injection failed",
        ),
    ]

    return ChainResult(
        chain_id="test-chain",
        chain_name="Test Chain",
        target_config={"audit_transport": "stdio"},
        step_outputs=outputs,
        trust_boundaries_crossed=["client-to-server"],
        started_at=now,
        finished_at=now,
        dry_run=False,
    )


_BASE_CONFIG: dict = {
    "chain_file": "/tmp/test-chain.yaml",
    "transport": "stdio",
    "command": "python server.py --flag",
    "inject_model": "anthropic/claude-sonnet-4-20250514",
}


class TestChainAdapter:
    """Tests for ChainAdapter orchestration glue."""

    async def test_chain_run_success(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Mock execute_chain returns success=True -> COMPLETED."""
        await runner.start()
        chain_def = _make_chain_def()
        chain_result = _make_chain_result(success=True)

        adapter = ChainAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.chain.adapter.load_chain", return_value=chain_def),
            patch("q_ai.chain.adapter.execute_chain", return_value=chain_result),
            patch("q_ai.chain.adapter.persist_chain"),
        ):
            result = await adapter.run()

        assert isinstance(result, ChainAdapterResult)
        assert result.success is True
        assert result.step_count == 2
        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_chain_run_failure(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Mock execute_chain returns success=False -> FAILED status."""
        await runner.start()
        chain_def = _make_chain_def()
        chain_result = _make_chain_result(success=False)

        adapter = ChainAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.chain.adapter.load_chain", return_value=chain_def),
            patch("q_ai.chain.adapter.execute_chain", return_value=chain_result),
            patch("q_ai.chain.adapter.persist_chain"),
        ):
            result = await adapter.run()

        assert result.success is False
        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.FAILED

    async def test_chain_validation_error(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Mock load_chain raises ChainValidationError -> FAILED, raises."""
        await runner.start()
        adapter = ChainAdapter(runner, _BASE_CONFIG)

        with (
            patch(
                "q_ai.chain.adapter.load_chain",
                side_effect=ChainValidationError("bad chain"),
            ),
            pytest.raises(ChainValidationError, match="bad chain"),
        ):
            await adapter.run()

        # Find child run
        with get_connection(db_path) as conn:
            children = conn.execute(
                "SELECT id FROM runs WHERE parent_run_id = ?", (runner.run_id,)
            ).fetchall()
            assert len(children) == 1
            child = get_run(conn, children[0]["id"])
        assert child is not None
        assert child.status == RunStatus.FAILED

    async def test_chain_persist_called_with_child_id(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Verify persist_chain called with run_id=child_id."""
        await runner.start()
        chain_def = _make_chain_def()
        chain_result = _make_chain_result(success=True)

        adapter = ChainAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.chain.adapter.load_chain", return_value=chain_def),
            patch("q_ai.chain.adapter.execute_chain", return_value=chain_result),
            patch("q_ai.chain.adapter.persist_chain") as mock_persist,
        ):
            result = await adapter.run()

        mock_persist.assert_called_once()
        call_kwargs = mock_persist.call_args
        assert call_kwargs.kwargs["run_id"] == result.run_id

    async def test_chain_target_config_built_correctly(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Verify TargetConfig fields from config dict."""
        await runner.start()
        chain_def = _make_chain_def()
        chain_result = _make_chain_result(success=True)

        captured_config = {}

        async def capture_execute(chain, target_config):
            captured_config["transport"] = target_config.audit_transport
            captured_config["command"] = target_config.audit_command
            captured_config["url"] = target_config.audit_url
            captured_config["model"] = target_config.inject_model
            return chain_result

        config = {
            "chain_file": "/tmp/test.yaml",
            "transport": "sse",
            "url": "http://localhost:3000/sse",
            "inject_model": "openai/gpt-4",
        }

        adapter = ChainAdapter(runner, config)

        with (
            patch("q_ai.chain.adapter.load_chain", return_value=chain_def),
            patch("q_ai.chain.adapter.execute_chain", side_effect=capture_execute),
            patch("q_ai.chain.adapter.persist_chain"),
        ):
            await adapter.run()

        assert captured_config["transport"] == "sse"
        assert captured_config["command"] is None
        assert captured_config["url"] == "http://localhost:3000/sse"
        assert captured_config["model"] == "openai/gpt-4"
