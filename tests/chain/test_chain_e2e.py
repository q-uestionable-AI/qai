"""End-to-end tests for chain execution via executor.

Tests real audit steps against fixture servers and mocked inject steps.
Integration tests requiring fixture servers are skipped.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.chain.executor import execute_chain
from q_ai.chain.executor_models import StepOutput, TargetConfig
from q_ai.chain.models import (
    ChainCategory,
    ChainDefinition,
    ChainStep,
    StepStatus,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VULN_INJECTION_SERVER = _REPO_ROOT / "fixtures" / "vulnerable_servers" / "vuln_injection.py"
PYTHON = sys.executable

_PATCH_INJECT = "q_ai.chain.executor.execute_inject_step"

pytestmark = pytest.mark.skip(reason="requires fixture server")


def _make_mcp_compromise_chain(with_inputs: bool = False) -> ChainDefinition:
    """Build a chain modeled on mcp_server_compromise template."""
    inject_inputs: dict = {}
    if with_inputs:
        inject_inputs = {"tool_name": "$scan-injection.vulnerable_tool"}

    return ChainDefinition(
        id="test-mcp-compromise",
        name="Test MCP Server Compromise",
        category=ChainCategory.MCP_ECOSYSTEM,
        description="E2E test chain",
        steps=[
            ChainStep(
                id="scan-injection",
                name="Identify command injection vulnerability",
                module="audit",
                technique="injection",
                trust_boundary="client-to-server",
                on_success="poison-tool",
                on_failure="abort",
            ),
            ChainStep(
                id="poison-tool",
                name="Poison vulnerable tool description",
                module="inject",
                technique="description_poisoning",
                trust_boundary="agent-to-tool",
                terminal=True,
                inputs=inject_inputs,
            ),
        ],
    )


def _mock_inject_output(
    step_id: str = "poison-tool",
    success: bool = True,
) -> StepOutput:
    """Build a mock inject StepOutput."""
    return StepOutput(
        step_id=step_id,
        module="inject",
        technique="description_poisoning",
        success=success,
        status=StepStatus.SUCCESS if success else StepStatus.FAILED,
        artifacts={
            "best_outcome": "full_compliance",
            "working_payload": "test_payload",
            "working_technique": "description_poisoning",
            "compliance_rate": "100",
        },
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


class TestChainE2EAuditStep:
    """E2E tests with real audit steps against fixture servers."""

    @pytest.mark.asyncio
    async def test_audit_step_produces_real_findings(self):
        """First step of chain finds real injection vulnerabilities."""
        chain = _make_mcp_compromise_chain()
        config = TargetConfig(
            audit_transport="stdio",
            audit_command=[PYTHON, str(VULN_INJECTION_SERVER)],
            inject_model="test-model",
        )

        mock_inject = AsyncMock(return_value=_mock_inject_output())

        with patch(_PATCH_INJECT, mock_inject):
            result = await execute_chain(chain, config)

        audit_output = result.step_outputs[0]
        assert audit_output.step_id == "scan-injection"
        assert audit_output.success is True
        assert audit_output.scan_result is not None
        assert len(audit_output.scan_result.findings) >= 1
        assert audit_output.artifacts["finding_count"] != "0"
        assert audit_output.artifacts["vulnerable_tool"] != ""


class TestChainE2EMockedInject:
    """Full chain E2E with real audit + mocked inject."""

    @pytest.mark.asyncio
    async def test_full_chain_success(self):
        """Full chain completes with real audit and mocked inject."""
        chain = _make_mcp_compromise_chain()
        config = TargetConfig(
            audit_transport="stdio",
            audit_command=[PYTHON, str(VULN_INJECTION_SERVER)],
            inject_model="test-model",
        )

        mock_inject = AsyncMock(return_value=_mock_inject_output())

        with patch(_PATCH_INJECT, mock_inject):
            result = await execute_chain(chain, config)

        assert result.success is True
        assert len(result.step_outputs) == 2
        assert result.dry_run is False
        assert result.finished_at is not None
        assert result.trust_boundaries_crossed == ["client-to-server", "agent-to-tool"]

    @pytest.mark.asyncio
    async def test_artifact_flow_between_steps(self):
        """Audit artifacts resolve in inject step inputs via $step_id.artifact_name."""
        chain = _make_mcp_compromise_chain(with_inputs=True)
        config = TargetConfig(
            audit_transport="stdio",
            audit_command=[PYTHON, str(VULN_INJECTION_SERVER)],
            inject_model="test-model",
        )

        captured_resolved: dict = {}

        async def capturing_inject(step, tc, resolved):
            captured_resolved.update(resolved)
            return _mock_inject_output()

        with patch(_PATCH_INJECT, side_effect=capturing_inject):
            result = await execute_chain(chain, config)

        assert result.success is True
        assert "tool_name" in captured_resolved
        assert captured_resolved["tool_name"] != ""
        assert not captured_resolved["tool_name"].startswith("$")

    @pytest.mark.asyncio
    async def test_json_report_contains_evidence(self, tmp_path: Path):
        """JSON report output contains all step evidence."""
        from q_ai.chain.executor import write_chain_report

        chain = _make_mcp_compromise_chain()
        config = TargetConfig(
            audit_transport="stdio",
            audit_command=[PYTHON, str(VULN_INJECTION_SERVER)],
            inject_model="test-model",
        )

        mock_inject = AsyncMock(return_value=_mock_inject_output())

        with patch(_PATCH_INJECT, mock_inject):
            result = await execute_chain(chain, config)

        report_path = tmp_path / "report.json"
        write_chain_report(result, report_path)

        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))

        assert data["chain_id"] == "test-mcp-compromise"
        assert data["success"] is True
        assert data["dry_run"] is False
        assert len(data["step_outputs"]) == 2

    @pytest.mark.asyncio
    async def test_inject_failure_aborts_chain(self):
        """When inject step fails, chain reports failure."""
        chain = _make_mcp_compromise_chain()
        config = TargetConfig(
            audit_transport="stdio",
            audit_command=[PYTHON, str(VULN_INJECTION_SERVER)],
            inject_model="test-model",
        )

        failed_inject = AsyncMock(return_value=_mock_inject_output(success=False))

        with patch(_PATCH_INJECT, failed_inject):
            result = await execute_chain(chain, config)

        assert result.success is False
        assert len(result.step_outputs) == 2
        assert result.step_outputs[0].success is True
        assert result.step_outputs[1].success is False
