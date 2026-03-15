"""Tests for the audit adapter."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.audit.adapter import AuditAdapter, AuditResult
from q_ai.audit.orchestrator import ScanResult
from q_ai.core.db import get_connection, get_run
from q_ai.core.models import RunStatus
from q_ai.core.schema import migrate
from q_ai.mcp.models import ScanFinding, Severity
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
        config={"target_id": "t1"},
        db_path=db_path,
    )


def _make_scan_result(num_findings: int = 1) -> ScanResult:
    """Build a synthetic ScanResult for testing."""
    findings = [
        ScanFinding(
            rule_id=f"MCP05-{i:03d}",
            category="command_injection",
            title=f"Finding {i}",
            description=f"Description {i}",
            severity=Severity.HIGH,
            tool_name=f"tool_{i}",
            evidence="test evidence",
            remediation="fix it",
        )
        for i in range(num_findings)
    ]
    return ScanResult(
        findings=findings,
        server_info={"name": "test-server", "version": "1.0"},
        tools_scanned=5,
        scanners_run=["injection"],
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )


class TestAuditAdapter:
    """Tests for AuditAdapter orchestration glue."""

    async def test_run_creates_child_run(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify child run created with module='audit' and parent_run_id set."""
        await runner.start()

        scan_result = _make_scan_result()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.init_result.serverInfo.name = "test-server"

        adapter = AuditAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch("q_ai.audit.adapter._build_connection", return_value=mock_conn),
            patch("q_ai.audit.adapter.run_scan", return_value=scan_result),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.module == "audit"
        assert child.parent_run_id == runner.run_id

    async def test_run_persists_scan_result(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify audit_scans row and findings rows created with correct run_id."""
        await runner.start()

        scan_result = _make_scan_result(num_findings=2)
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.init_result.serverInfo.name = "test-server"

        adapter = AuditAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch("q_ai.audit.adapter._build_connection", return_value=mock_conn),
            patch("q_ai.audit.adapter.run_scan", return_value=scan_result),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            scans = conn.execute(
                "SELECT * FROM audit_scans WHERE run_id = ?", (result.run_id,)
            ).fetchall()
            assert len(scans) == 1

            findings = conn.execute(
                "SELECT * FROM findings WHERE run_id = ?", (result.run_id,)
            ).fetchall()
            assert len(findings) == 2

    async def test_run_emits_finding_events(self, db_path: Path) -> None:
        """Verify finding events emitted via ws_manager."""
        ws_manager = AsyncMock()
        runner = WorkflowRunner(
            workflow_id="assess",
            config={},
            ws_manager=ws_manager,
            db_path=db_path,
        )
        await runner.start()

        scan_result = _make_scan_result(num_findings=2)
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.init_result.serverInfo.name = "test-server"

        adapter = AuditAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch("q_ai.audit.adapter._build_connection", return_value=mock_conn),
            patch("q_ai.audit.adapter.run_scan", return_value=scan_result),
        ):
            await adapter.run()

        # Check that finding events were broadcast
        finding_calls = [
            call
            for call in ws_manager.broadcast.call_args_list
            if call.args[0].get("type") == "finding"
        ]
        assert len(finding_calls) == 2

    async def test_run_sets_completed_on_success(
        self, runner: WorkflowRunner, db_path: Path
    ) -> None:
        """Verify child run status=COMPLETED after run()."""
        await runner.start()

        scan_result = _make_scan_result()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.init_result.serverInfo.name = "test-server"

        adapter = AuditAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch("q_ai.audit.adapter._build_connection", return_value=mock_conn),
            patch("q_ai.audit.adapter.run_scan", return_value=scan_result),
        ):
            result = await adapter.run()

        with get_connection(db_path) as conn:
            child = get_run(conn, result.run_id)
        assert child is not None
        assert child.status == RunStatus.COMPLETED

    async def test_run_sets_failed_on_error(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Mock run_scan to raise, verify child run status=FAILED."""
        await runner.start()

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.init_result.serverInfo.name = "test-server"

        adapter = AuditAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch("q_ai.audit.adapter._build_connection", return_value=mock_conn),
            patch(
                "q_ai.audit.adapter.run_scan",
                side_effect=RuntimeError("scan failed"),
            ),
            pytest.raises(RuntimeError, match="scan failed"),
        ):
            await adapter.run()

        # Find the child run (most recent non-workflow run)
        with get_connection(db_path) as conn:
            children = conn.execute(
                "SELECT id FROM runs WHERE parent_run_id = ?", (runner.run_id,)
            ).fetchall()
            assert len(children) == 1
            child = get_run(conn, children[0]["id"])
        assert child is not None
        assert child.status == RunStatus.FAILED

    async def test_run_returns_audit_result(self, runner: WorkflowRunner, db_path: Path) -> None:
        """Verify AuditResult fields."""
        await runner.start()

        scan_result = _make_scan_result(num_findings=3)
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.init_result.serverInfo.name = "test-server"

        adapter = AuditAdapter(runner, {"transport": "stdio", "command": "python server.py"})

        with (
            patch("q_ai.audit.adapter._build_connection", return_value=mock_conn),
            patch("q_ai.audit.adapter.run_scan", return_value=scan_result),
        ):
            result = await adapter.run()

        assert isinstance(result, AuditResult)
        assert result.run_id
        assert result.scan_result is scan_result
        assert result.finding_count == 3
