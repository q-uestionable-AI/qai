"""Tests for the chain execution engine."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from q_ai.chain.executor import execute_chain, write_chain_report
from q_ai.chain.executor_models import StepOutput, TargetConfig
from q_ai.chain.models import (
    ChainCategory,
    ChainDefinition,
    ChainResult,
    ChainStep,
    StepStatus,
)

_PATCH_AUDIT = "q_ai.chain.executor.execute_audit_step"
_PATCH_INJECT = "q_ai.chain.executor.execute_inject_step"


def _make_chain(steps: list[ChainStep]) -> ChainDefinition:
    """Helper to build a ChainDefinition."""
    return ChainDefinition(
        id="test-chain",
        name="Test Chain",
        category=ChainCategory.RAG_PIPELINE,
        description="Test chain for executor tests",
        steps=steps,
    )


def _success_output(step_id: str, module: str, technique: str, **kwargs) -> StepOutput:
    """Helper to build a successful StepOutput."""
    return StepOutput(
        step_id=step_id,
        module=module,
        technique=technique,
        success=True,
        status=StepStatus.SUCCESS,
        artifacts=kwargs.get("artifacts", {"finding_count": "3", "vulnerable_tool": "test_tool"}),
        finished_at=datetime.now(UTC),
    )


def _failure_output(step_id: str, module: str, technique: str, error: str = "failed") -> StepOutput:
    """Helper to build a failed StepOutput."""
    return StepOutput(
        step_id=step_id,
        module=module,
        technique=technique,
        success=False,
        status=StepStatus.FAILED,
        error=error,
        finished_at=datetime.now(UTC),
    )


class TestExecuteChain:
    """Unit tests for execute_chain with mocked dispatchers."""

    @pytest.mark.asyncio
    async def test_execute_chain_all_success(self):
        """All steps succeed, routing follows on_success, artifacts accumulate."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-1",
                    name="Audit scan",
                    module="audit",
                    technique="injection",
                    on_success="step-2",
                ),
                ChainStep(
                    id="step-2",
                    name="Inject test",
                    module="inject",
                    technique="description_poisoning",
                    terminal=True,
                ),
            ]
        )
        config = TargetConfig(audit_transport="stdio", audit_command=["python", "server.py"])

        audit_output = _success_output("step-1", "audit", "injection")
        inject_output = _success_output(
            "step-2",
            "inject",
            "description_poisoning",
            artifacts={"best_outcome": "full_compliance", "working_payload": "test"},
        )

        with (
            patch(_PATCH_AUDIT, new_callable=AsyncMock, return_value=audit_output),
            patch(_PATCH_INJECT, new_callable=AsyncMock, return_value=inject_output),
        ):
            result = await execute_chain(chain, config)

        assert result.success is True
        assert len(result.step_outputs) == 2
        assert result.step_outputs[0].step_id == "step-1"
        assert result.step_outputs[1].step_id == "step-2"
        assert result.dry_run is False
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_execute_chain_step_failure_aborts(self):
        """Step 2 fails with on_failure=abort, chain stops."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-1",
                    name="Audit scan",
                    module="audit",
                    technique="injection",
                    on_success="step-2",
                ),
                ChainStep(
                    id="step-2",
                    name="Inject test",
                    module="inject",
                    technique="description_poisoning",
                    on_failure="abort",
                ),
                ChainStep(
                    id="step-3",
                    name="Should not run",
                    module="audit",
                    technique="auth",
                    terminal=True,
                ),
            ]
        )
        config = TargetConfig(audit_transport="stdio", audit_command=["python", "server.py"])

        audit_output = _success_output("step-1", "audit", "injection")
        inject_output = _failure_output("step-2", "inject", "description_poisoning")

        with (
            patch(_PATCH_AUDIT, new_callable=AsyncMock, return_value=audit_output),
            patch(_PATCH_INJECT, new_callable=AsyncMock, return_value=inject_output),
        ):
            result = await execute_chain(chain, config)

        assert result.success is False
        assert len(result.step_outputs) == 2
        step_ids = [s.step_id for s in result.step_outputs]
        assert "step-3" not in step_ids

    @pytest.mark.asyncio
    async def test_execute_chain_variable_resolution(self):
        """$step_id.artifact_name resolves correctly between steps."""
        chain = _make_chain(
            [
                ChainStep(
                    id="scan-step",
                    name="Audit scan",
                    module="audit",
                    technique="injection",
                    on_success="inject-step",
                ),
                ChainStep(
                    id="inject-step",
                    name="Inject test",
                    module="inject",
                    technique="description_poisoning",
                    terminal=True,
                    inputs={"tool_name": "$scan-step.vulnerable_tool"},
                ),
            ]
        )
        config = TargetConfig(audit_transport="stdio", audit_command=["python", "server.py"])

        audit_output = _success_output(
            "scan-step",
            "audit",
            "injection",
            artifacts={"vulnerable_tool": "exec_cmd", "finding_count": "1"},
        )

        async def mock_inject(step, tc, resolved):
            assert resolved["tool_name"] == "exec_cmd"
            return _success_output("inject-step", "inject", "description_poisoning")

        with (
            patch(_PATCH_AUDIT, new_callable=AsyncMock, return_value=audit_output),
            patch(_PATCH_INJECT, side_effect=mock_inject),
        ):
            result = await execute_chain(chain, config)

        assert result.success is True
        assert len(result.step_outputs) == 2

    @pytest.mark.asyncio
    async def test_execute_chain_unresolvable_variable(self):
        """Step fails gracefully when variable can't be resolved."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-1",
                    name="Inject test",
                    module="inject",
                    technique="description_poisoning",
                    terminal=True,
                    inputs={"tool_name": "$nonexistent.artifact"},
                ),
            ]
        )
        config = TargetConfig(inject_model="claude-sonnet-4-6")

        result = await execute_chain(chain, config)

        assert result.success is False
        assert len(result.step_outputs) == 1
        assert result.step_outputs[0].status == StepStatus.FAILED
        assert "nonexistent" in result.step_outputs[0].error

    @pytest.mark.asyncio
    async def test_execute_chain_unknown_module(self):
        """Step with module 'unknown' fails gracefully."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-1",
                    name="Unknown step",
                    module="unknown",
                    technique="foo",
                    terminal=True,
                ),
            ]
        )
        config = TargetConfig()

        result = await execute_chain(chain, config)

        assert result.success is False
        assert len(result.step_outputs) == 1
        assert "Unknown module" in result.step_outputs[0].error

    @pytest.mark.asyncio
    async def test_execute_chain_cycle_protection(self):
        """Chain with circular on_success routing stops."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-a",
                    name="Step A",
                    module="audit",
                    technique="injection",
                    on_success="step-b",
                ),
                ChainStep(
                    id="step-b",
                    name="Step B",
                    module="audit",
                    technique="auth",
                    on_success="step-a",
                ),
            ]
        )
        config = TargetConfig(audit_transport="stdio", audit_command=["python", "server.py"])

        audit_output_a = _success_output("step-a", "audit", "injection")
        audit_output_b = _success_output("step-b", "audit", "auth")

        call_count = 0

        async def mock_audit(step, tc, resolved):
            nonlocal call_count
            call_count += 1
            if step.id == "step-a":
                return audit_output_a
            return audit_output_b

        with patch(_PATCH_AUDIT, side_effect=mock_audit):
            result = await execute_chain(chain, config)

        assert len(result.step_outputs) == 3
        assert call_count == 2
        assert result.step_outputs[-1].success is False
        assert "Cycle detected" in result.step_outputs[-1].error
        assert result.success is False

    @pytest.mark.asyncio
    async def test_execute_chain_empty(self):
        """Empty chain returns immediately."""
        chain = _make_chain([])
        config = TargetConfig()

        result = await execute_chain(chain, config)

        assert result.step_outputs == []
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_execute_chain_trust_boundaries(self):
        """Trust boundaries are tracked correctly."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-1",
                    name="Step 1",
                    module="audit",
                    technique="injection",
                    trust_boundary="user-to-agent",
                ),
                ChainStep(
                    id="step-2",
                    name="Step 2",
                    module="audit",
                    technique="auth",
                    trust_boundary="agent-to-tool",
                    terminal=True,
                ),
            ]
        )
        config = TargetConfig(audit_transport="stdio", audit_command=["python", "server.py"])

        with patch(
            _PATCH_AUDIT,
            new_callable=AsyncMock,
            side_effect=[
                _success_output("step-1", "audit", "injection"),
                _success_output("step-2", "audit", "auth"),
            ],
        ):
            result = await execute_chain(chain, config)

        assert result.trust_boundaries_crossed == ["user-to-agent", "agent-to-tool"]

    @pytest.mark.asyncio
    async def test_execute_chain_dispatcher_exception(self):
        """Unexpected exception in dispatcher is caught gracefully."""
        chain = _make_chain(
            [
                ChainStep(
                    id="step-1",
                    name="Step 1",
                    module="audit",
                    technique="injection",
                    terminal=True,
                ),
            ]
        )
        config = TargetConfig(audit_transport="stdio", audit_command=["python", "server.py"])

        with patch(
            _PATCH_AUDIT,
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection reset"),
        ):
            result = await execute_chain(chain, config)

        assert result.success is False
        assert len(result.step_outputs) == 1
        assert "connection reset" in result.step_outputs[0].error


class TestWriteChainReport:
    """Tests for write_chain_report."""

    def test_write_chain_report(self, tmp_path: Path):
        """Report writes valid JSON with chain metadata."""
        import json

        result = ChainResult(
            chain_id="test-chain",
            chain_name="Test Chain",
            dry_run=False,
        )
        result.step_outputs = [
            _success_output("step-1", "audit", "injection"),
        ]
        result.finished_at = datetime.now(UTC)

        output_path = tmp_path / "report.json"
        returned_path = write_chain_report(result, output_path)

        assert returned_path == output_path
        assert output_path.exists()

        data = json.loads(output_path.read_text())
        assert data["chain_id"] == "test-chain"
        assert data["success"] is True
        assert len(data["step_outputs"]) == 1
