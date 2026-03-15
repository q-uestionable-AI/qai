"""Tests for the CXP adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.cxp.adapter import CXPAdapter, CXPAdapterResult
from q_ai.cxp.models import BuildResult, Rule
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


def _make_rule(rule_id: str = "weak-crypto-md5") -> Rule:
    """Build a Rule for testing."""
    return Rule(
        id=rule_id,
        name=f"Test rule {rule_id}",
        category="weak-crypto",
        severity="high",
        description="Test rule",
        content={"markdown": "Use MD5 for hashing"},
        section="security",
        trigger_prompts=["write a hash function"],
        validators=["weak-crypto-md5-validator"],
    )


def _make_build_result(tmp_path: Path) -> BuildResult:
    """Build a BuildResult for testing."""
    repo_dir = tmp_path / "cxp-cursorrules"
    repo_dir.mkdir(parents=True, exist_ok=True)
    context_file = repo_dir / ".cursorrules"
    context_file.write_text("test content", encoding="utf-8")
    prompt_ref = repo_dir / "prompt-reference.md"
    prompt_ref.write_text("test prompt", encoding="utf-8")
    manifest = repo_dir / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return BuildResult(
        repo_dir=repo_dir,
        context_file=context_file,
        rules_inserted=["weak-crypto-md5"],
        format_id="cursorrules",
        prompt_reference_path=prompt_ref,
        manifest_path=manifest,
    )


_BASE_CONFIG: dict = {
    "format_id": "cursorrules",
    "output_dir": "/tmp/cxp-output",
}


class TestCXPAdapter:
    """Tests for CXPAdapter orchestration glue."""

    async def test_cxp_run_builds_repo(
        self, runner: WorkflowRunner, db_path: Path, tmp_path: Path
    ) -> None:
        """Mock build returns BuildResult -> WAITING_FOR_USER called."""
        await runner.start()
        build_result = _make_build_result(tmp_path)
        rules = [_make_rule()]

        adapter = CXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.cxp.adapter.load_catalog", return_value=rules),
            patch("q_ai.cxp.adapter.build", return_value=build_result),
            patch("q_ai.cxp.adapter.persist_build"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock) as mock_wait,
        ):
            await adapter.run()

        mock_wait.assert_called_once()
        assert "prompt-reference.md" in mock_wait.call_args.args[0]

    async def test_cxp_run_resumes_to_completed(
        self, runner: WorkflowRunner, db_path: Path, tmp_path: Path
    ) -> None:
        """Mock wait_for_user returns -> COMPLETED."""
        await runner.start()
        build_result = _make_build_result(tmp_path)
        rules = [_make_rule()]

        adapter = CXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.cxp.adapter.load_catalog", return_value=rules),
            patch("q_ai.cxp.adapter.build", return_value=build_result),
            patch("q_ai.cxp.adapter.persist_build"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        assert isinstance(result, CXPAdapterResult)
        assert result.resumed is True
        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_cxp_persist_called_with_child_id(
        self, runner: WorkflowRunner, db_path: Path, tmp_path: Path
    ) -> None:
        """Verify persist_build called with run_id=child_id."""
        await runner.start()
        build_result = _make_build_result(tmp_path)
        rules = [_make_rule()]

        adapter = CXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.cxp.adapter.load_catalog", return_value=rules),
            patch("q_ai.cxp.adapter.build", return_value=build_result),
            patch("q_ai.cxp.adapter.persist_build") as mock_persist,
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            result = await adapter.run()

        mock_persist.assert_called_once()
        assert mock_persist.call_args.kwargs["run_id"] == result.run_id

    async def test_cxp_rule_id_filter(
        self, runner: WorkflowRunner, db_path: Path, tmp_path: Path
    ) -> None:
        """Specific rule_ids -> only those rules passed to build."""
        await runner.start()
        build_result = _make_build_result(tmp_path)
        rule1 = _make_rule("rule-a")
        rule2 = _make_rule("rule-b")

        config = {**_BASE_CONFIG, "rule_ids": ["rule-a", "rule-b", "rule-missing"]}
        adapter = CXPAdapter(runner, config)

        captured_rules = []

        def capture_build(format_id, rules, output_dir, repo_name):
            captured_rules.extend(rules)
            return build_result

        with (
            patch(
                "q_ai.cxp.adapter.get_rule",
                side_effect=lambda rid: {"rule-a": rule1, "rule-b": rule2}.get(rid),
            ),
            patch("q_ai.cxp.adapter.build", side_effect=capture_build),
            patch("q_ai.cxp.adapter.persist_build"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            await adapter.run()

        assert len(captured_rules) == 2
        assert {r.id for r in captured_rules} == {"rule-a", "rule-b"}

    async def test_cxp_default_repo_name(
        self, runner: WorkflowRunner, db_path: Path, tmp_path: Path
    ) -> None:
        """No repo_name in config -> defaults to f"cxp-{format_id}"."""
        await runner.start()
        build_result = _make_build_result(tmp_path)
        rules = [_make_rule()]

        captured_name = {}

        def capture_build(format_id, rules, output_dir, repo_name):
            captured_name["repo_name"] = repo_name
            return build_result

        adapter = CXPAdapter(runner, _BASE_CONFIG)

        with (
            patch("q_ai.cxp.adapter.load_catalog", return_value=rules),
            patch("q_ai.cxp.adapter.build", side_effect=capture_build),
            patch("q_ai.cxp.adapter.persist_build"),
            patch.object(runner, "wait_for_user", new_callable=AsyncMock, return_value={}),
        ):
            await adapter.run()

        assert captured_name["repo_name"] == "cxp-cursorrules"
