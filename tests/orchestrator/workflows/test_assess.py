"""Tests for the assess MCP server workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.core.models import RunStatus
from q_ai.orchestrator.workflows.assess import assess_mcp_server


def _make_runner(run_id: str = "run-1") -> MagicMock:
    """Create a mock WorkflowRunner."""
    runner = MagicMock()
    runner.run_id = run_id
    runner._db_path = None
    runner.resolve_target = AsyncMock(return_value=MagicMock(id="target-1"))
    runner.emit_progress = AsyncMock()
    runner.complete = AsyncMock()
    runner.fail = AsyncMock()
    return runner


def _base_config() -> dict:
    """Create a minimal valid config."""
    return {
        "target_id": "target-1",
        "transport": "stdio",
        "command": "echo hello",
        "url": None,
        "audit": {"checks": None},
        "inject": {"model": "openai/gpt-4", "rounds": 1},
        "proxy": {"intercept": False},
    }


_AUDIT_PATCH = "q_ai.orchestrator.workflows.assess.AuditAdapter"
_INJECT_PATCH = "q_ai.orchestrator.workflows.assess.InjectAdapter"
_PROXY_PATCH = "q_ai.orchestrator.workflows.assess.ProxyAdapter"


class TestAssessWorkflow:
    """Tests for the assess_mcp_server workflow executor."""

    async def test_full_workflow_success(self) -> None:
        """All 3 adapters succeed -> runner.complete(COMPLETED) called."""
        runner = _make_runner()
        config = _base_config()

        mock_scan_result = MagicMock()
        mock_scan_result.findings = []
        mock_audit_result = MagicMock()
        mock_audit_result.scan_result = mock_scan_result
        mock_audit_result.finding_count = 0

        mock_inject_result = MagicMock()
        mock_inject_result.finding_count = 0

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(return_value=mock_audit_result)
            MockInject.return_value.run = AsyncMock(return_value=mock_inject_result)
            MockProxy.return_value.start = AsyncMock()
            MockProxy.return_value.stop = AsyncMock()

            await assess_mcp_server(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.COMPLETED)

    async def test_audit_failure_continues(self) -> None:
        """AuditAdapter.run() raises -> inject still runs -> PARTIAL."""
        runner = _make_runner()
        config = _base_config()

        mock_inject_result = MagicMock()
        mock_inject_result.finding_count = 0

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(side_effect=RuntimeError("audit fail"))
            MockInject.return_value.run = AsyncMock(return_value=mock_inject_result)
            MockProxy.return_value.start = AsyncMock()
            MockProxy.return_value.stop = AsyncMock()

            await assess_mcp_server(runner, config)

        MockInject.return_value.run.assert_awaited_once()
        runner.complete.assert_awaited_once_with(RunStatus.PARTIAL)

    async def test_proxy_failure_continues(self) -> None:
        """ProxyAdapter.start() raises -> inject still runs -> PARTIAL."""
        runner = _make_runner()
        config = _base_config()

        mock_scan_result = MagicMock()
        mock_scan_result.findings = []
        mock_audit_result = MagicMock()
        mock_audit_result.scan_result = mock_scan_result
        mock_audit_result.finding_count = 0

        mock_inject_result = MagicMock()
        mock_inject_result.finding_count = 0

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(return_value=mock_audit_result)
            MockInject.return_value.run = AsyncMock(return_value=mock_inject_result)
            MockProxy.return_value.start = AsyncMock(side_effect=RuntimeError("proxy fail"))

            await assess_mcp_server(runner, config)

        MockInject.return_value.run.assert_awaited_once()
        runner.complete.assert_awaited_once_with(RunStatus.PARTIAL)

    async def test_inject_failure_stops_proxy(self) -> None:
        """InjectAdapter.run() raises -> ProxyAdapter.stop() still called -> PARTIAL."""
        runner = _make_runner()
        config = _base_config()

        mock_scan_result = MagicMock()
        mock_scan_result.findings = []
        mock_audit_result = MagicMock()
        mock_audit_result.scan_result = mock_scan_result
        mock_audit_result.finding_count = 0

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(return_value=mock_audit_result)
            MockInject.return_value.run = AsyncMock(side_effect=RuntimeError("inject fail"))
            MockProxy.return_value.start = AsyncMock()
            MockProxy.return_value.stop = AsyncMock()

            await assess_mcp_server(runner, config)

        MockProxy.return_value.stop.assert_awaited_once()
        runner.complete.assert_awaited_once_with(RunStatus.PARTIAL)

    async def test_all_stages_fail(self) -> None:
        """All three raise -> complete(PARTIAL)."""
        runner = _make_runner()
        config = _base_config()

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(side_effect=RuntimeError("audit"))
            MockInject.return_value.run = AsyncMock(side_effect=RuntimeError("inject"))
            MockProxy.return_value.start = AsyncMock(side_effect=RuntimeError("proxy"))

            await assess_mcp_server(runner, config)

        runner.complete.assert_awaited_once_with(RunStatus.PARTIAL)

    async def test_no_audit_findings_in_inject_config(self) -> None:
        """Inject adapter config does not contain audit_findings passthrough."""
        runner = _make_runner()
        config = _base_config()

        mock_audit_result = MagicMock()
        mock_audit_result.finding_count = 1

        mock_inject_result = MagicMock()
        mock_inject_result.finding_count = 0

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(return_value=mock_audit_result)
            MockInject.return_value.run = AsyncMock(return_value=mock_inject_result)
            MockProxy.return_value.start = AsyncMock()
            MockProxy.return_value.stop = AsyncMock()

            await assess_mcp_server(runner, config)

        # Inject adapter now queries findings via service layer, not config passthrough
        inject_call_config = MockInject.call_args[0][1]
        assert "audit_findings" not in inject_call_config

    async def test_config_routed_to_adapters(self) -> None:
        """Each adapter gets the correct config subset."""
        runner = _make_runner()
        config = _base_config()

        mock_scan_result = MagicMock()
        mock_scan_result.findings = []
        mock_audit_result = MagicMock()
        mock_audit_result.scan_result = mock_scan_result
        mock_audit_result.finding_count = 0

        mock_inject_result = MagicMock()
        mock_inject_result.finding_count = 0

        with (
            patch(_AUDIT_PATCH) as MockAudit,
            patch(_INJECT_PATCH) as MockInject,
            patch(_PROXY_PATCH) as MockProxy,
        ):
            MockAudit.return_value.run = AsyncMock(return_value=mock_audit_result)
            MockInject.return_value.run = AsyncMock(return_value=mock_inject_result)
            MockProxy.return_value.start = AsyncMock()
            MockProxy.return_value.stop = AsyncMock()

            await assess_mcp_server(runner, config)

        # AuditAdapter config should have transport, command, url, target_id, checks
        audit_config = MockAudit.call_args[0][1]
        assert audit_config["transport"] == "stdio"
        assert audit_config["command"] == "echo hello"
        assert audit_config["url"] is None
        assert audit_config["target_id"] == "target-1"
        assert "checks" in audit_config

        # InjectAdapter config should have transport, command, url, target_id, model,
        # rounds
        inject_config = MockInject.call_args[0][1]
        assert inject_config["transport"] == "stdio"
        assert inject_config["command"] == "echo hello"
        assert inject_config["url"] is None
        assert inject_config["target_id"] == "target-1"
        assert inject_config["model"] == "openai/gpt-4"
        assert inject_config["rounds"] == 1

        # ProxyAdapter config should have transport, command, url, target_id, intercept
        proxy_config = MockProxy.call_args[0][1]
        assert proxy_config["transport"] == "stdio"
        assert proxy_config["command"] == "echo hello"
        assert proxy_config["url"] is None
        assert proxy_config["target_id"] == "target-1"
        assert "intercept" in proxy_config
