"""Tests for the RXP adapter."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.orchestrator.runner import WorkflowRunner
from q_ai.rxp.adapter import RXPAdapter, RXPAdapterResult
from q_ai.rxp.models import (
    CorpusDocument,
    DomainProfile,
    ValidationResult,
)


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


def _make_validation_result(retrieval_rate: float = 0.6) -> ValidationResult:
    """Build a ValidationResult for testing."""
    return ValidationResult(
        model_id="minilm-l6",
        total_queries=5,
        poison_retrievals=int(5 * retrieval_rate),
        retrieval_rate=retrieval_rate,
        mean_poison_rank=2.5 if retrieval_rate > 0 else None,
        query_results=[],
    )


def _make_profile() -> DomainProfile:
    """Build a DomainProfile for testing."""
    return DomainProfile(
        id="hr-policy",
        name="HR Policy",
        description="Test profile",
        corpus_dir="/tmp/profiles/hr-policy",
        queries=["What is the vacation policy?", "How do I submit PTO?"],
    )


_BASE_CONFIG: dict = {
    "model_id": "minilm-l6",
    "profile_id": "hr-policy",
    "top_k": 5,
}


@pytest.fixture
def _mock_rxp_deps():
    """Mock RXP optional deps and the validator module.

    The validator module transitively imports chromadb and
    sentence_transformers at module level, which are not available in CI.
    We mock the entire dependency chain so the adapter's lazy import
    of validate_retrieval succeeds.
    """
    # Track modules we inject so we can clean up
    injected: list[str] = []
    dep_mods = [
        "chromadb",
        "sentence_transformers",
        "numpy",
    ]
    for mod_name in dep_mods:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()
            injected.append(mod_name)

    # Also clear any cached validator-related modules so they re-import
    validator_mods = [
        k
        for k in sys.modules
        if k.startswith("q_ai.rxp.collection")
        or k.startswith("q_ai.rxp.embedder")
        or k.startswith("q_ai.rxp.validator")
    ]
    saved: dict[str, ModuleType] = {}
    for k in validator_mods:
        saved[k] = sys.modules.pop(k)

    yield

    # Restore
    for mod_name in injected:
        sys.modules.pop(mod_name, None)
    for k, v in saved.items():
        sys.modules[k] = v


class TestRXPAdapter:
    """Tests for RXPAdapter orchestration glue."""

    async def test_rxp_run_with_profile(
        self, runner: WorkflowRunner, db_path: Path, _mock_rxp_deps: None
    ) -> None:
        """Mock profile load + validate_retrieval -> COMPLETED."""
        await runner.start()
        profile = _make_profile()
        val_result = _make_validation_result(retrieval_rate=0.6)
        corpus = [CorpusDocument(id="doc1", text="test", source="test.txt")]
        poison = [CorpusDocument(id="poison1", text="evil", source="p.txt", is_poison=True)]

        adapter = RXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.rxp.profiles.get_profile", return_value=profile),
            patch("q_ai.rxp.profiles.load_corpus", return_value=corpus),
            patch("q_ai.rxp.profiles.load_poison", return_value=poison),
            patch("q_ai.rxp._deps.require_rxp_deps"),
            patch("q_ai.rxp.validator.validate_retrieval", return_value=val_result),
            patch("q_ai.rxp.adapter.persist_validation"),
        ):
            result = await adapter.run()

        assert isinstance(result, RXPAdapterResult)
        assert result.retrieval_rate == 0.6
        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_rxp_persist_called_with_child_id(
        self, runner: WorkflowRunner, db_path: Path, _mock_rxp_deps: None
    ) -> None:
        """Verify persist_validation called with run_id=child_id."""
        await runner.start()
        profile = _make_profile()
        val_result = _make_validation_result()
        corpus = [CorpusDocument(id="doc1", text="test", source="test.txt")]
        poison = [CorpusDocument(id="poison1", text="evil", source="p.txt", is_poison=True)]

        adapter = RXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.rxp.profiles.get_profile", return_value=profile),
            patch("q_ai.rxp.profiles.load_corpus", return_value=corpus),
            patch("q_ai.rxp.profiles.load_poison", return_value=poison),
            patch("q_ai.rxp._deps.require_rxp_deps"),
            patch("q_ai.rxp.validator.validate_retrieval", return_value=val_result),
            patch("q_ai.rxp.adapter.persist_validation") as mock_persist,
        ):
            result = await adapter.run()

        mock_persist.assert_called_once()
        assert mock_persist.call_args.kwargs["run_id"] == result.run_id

    async def test_rxp_finding_emitted_on_retrieval(
        self, runner: WorkflowRunner, db_path: Path, _mock_rxp_deps: None
    ) -> None:
        """result.retrieval_rate > 0 -> emit_finding called."""
        await runner.start()
        ws_manager = AsyncMock()
        runner._ws_manager = ws_manager
        profile = _make_profile()
        val_result = _make_validation_result(retrieval_rate=0.8)
        corpus = [CorpusDocument(id="doc1", text="test", source="test.txt")]
        poison = [CorpusDocument(id="poison1", text="evil", source="p.txt", is_poison=True)]

        adapter = RXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.rxp.profiles.get_profile", return_value=profile),
            patch("q_ai.rxp.profiles.load_corpus", return_value=corpus),
            patch("q_ai.rxp.profiles.load_poison", return_value=poison),
            patch("q_ai.rxp._deps.require_rxp_deps"),
            patch("q_ai.rxp.validator.validate_retrieval", return_value=val_result),
            patch("q_ai.rxp.adapter.persist_validation"),
        ):
            await adapter.run()

        finding_calls = [
            call
            for call in ws_manager.broadcast.call_args_list
            if call.args[0].get("type") == "finding"
        ]
        assert len(finding_calls) == 1
        assert finding_calls[0].args[0]["module"] == "rxp"

    async def test_rxp_no_finding_on_zero_rate(
        self, runner: WorkflowRunner, db_path: Path, _mock_rxp_deps: None
    ) -> None:
        """retrieval_rate == 0 -> no finding emitted."""
        await runner.start()
        ws_manager = AsyncMock()
        runner._ws_manager = ws_manager
        profile = _make_profile()
        val_result = _make_validation_result(retrieval_rate=0.0)
        corpus = [CorpusDocument(id="doc1", text="test", source="test.txt")]
        poison = [CorpusDocument(id="poison1", text="evil", source="p.txt", is_poison=True)]

        adapter = RXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.rxp.profiles.get_profile", return_value=profile),
            patch("q_ai.rxp.profiles.load_corpus", return_value=corpus),
            patch("q_ai.rxp.profiles.load_poison", return_value=poison),
            patch("q_ai.rxp._deps.require_rxp_deps"),
            patch("q_ai.rxp.validator.validate_retrieval", return_value=val_result),
            patch("q_ai.rxp.adapter.persist_validation"),
        ):
            await adapter.run()

        finding_calls = [
            call
            for call in ws_manager.broadcast.call_args_list
            if call.args[0].get("type") == "finding"
        ]
        assert len(finding_calls) == 0

    async def test_rxp_missing_corpus_fails(self, runner: WorkflowRunner, db_path: Path) -> None:
        """No profile_id and no corpus_dir -> FAILED, raises."""
        await runner.start()
        config = {"model_id": "minilm-l6", "top_k": 5}
        adapter = RXPAdapter(runner, config)

        with pytest.raises(ValueError, match="corpus_dir is required"):
            await adapter.run()

        with get_connection(db_path) as conn:
            children = conn.execute(
                "SELECT id FROM runs WHERE parent_run_id = ?", (runner.run_id,)
            ).fetchall()
            assert len(children) == 1
            child = get_run(conn, children[0]["id"])
        assert child is not None
        assert child.status == RunStatus.FAILED
