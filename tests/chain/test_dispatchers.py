"""Tests for the chain step dispatchers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from q_ai.chain.executor import execute_audit_step, execute_inject_step
from q_ai.chain.executor_models import TargetConfig
from q_ai.chain.models import ChainStep, StepStatus


class TestExecuteAuditStepErrors:
    """Test audit dispatcher error handling."""

    @pytest.mark.asyncio
    async def test_missing_transport(self):
        """Returns FAILED when no audit transport is configured."""
        step = ChainStep(
            id="step-1",
            name="Audit",
            module="audit",
            technique="injection",
        )
        config = TargetConfig()

        output = await execute_audit_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "No audit transport" in output.error

    @pytest.mark.asyncio
    async def test_missing_command_for_stdio(self):
        """Returns FAILED when stdio transport has no command."""
        step = ChainStep(
            id="step-1",
            name="Audit",
            module="audit",
            technique="injection",
        )
        config = TargetConfig(audit_transport="stdio")

        output = await execute_audit_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "audit_command" in output.error

    @pytest.mark.asyncio
    async def test_unknown_transport(self):
        """Returns FAILED for unknown transport type."""
        step = ChainStep(
            id="step-1",
            name="Audit",
            module="audit",
            technique="injection",
        )
        config = TargetConfig(audit_transport="grpc", audit_command=["server"])

        output = await execute_audit_step(step, config, {})

        assert output.success is False
        assert "Unknown audit transport" in output.error


class TestExecuteInjectStepErrors:
    """Test inject dispatcher error handling."""

    @pytest.mark.asyncio
    async def test_unknown_technique(self):
        """Returns FAILED for unknown injection technique."""
        step = ChainStep(
            id="step-1",
            name="Inject",
            module="inject",
            technique="nonexistent_technique",
        )
        config = TargetConfig(inject_model="test-model")

        output = await execute_inject_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "Unknown injection technique" in output.error

    @pytest.mark.asyncio
    async def test_missing_model(self):
        """Returns FAILED when no inject model is configured."""
        step = ChainStep(
            id="step-1",
            name="Inject",
            module="inject",
            technique="description_poisoning",
        )
        config = TargetConfig()

        output = await execute_inject_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "No inject_model" in output.error

    @pytest.mark.asyncio
    async def test_applies_input_overrides(self):
        """Input overrides are applied to the template before campaign."""
        step = ChainStep(
            id="step-1",
            name="Inject",
            module="inject",
            technique="description_poisoning",
        )
        config = TargetConfig(inject_model="test-model")

        captured_templates = []

        async def mock_run_campaign(templates, model, rounds=1, output_dir=None):
            captured_templates.extend(templates)
            from q_ai.inject.models import Campaign

            return Campaign(id="test", name="test", model=model)

        overrides = {
            "tool_name": "overridden_tool",
            "tool_description": "overridden desc",
        }
        with patch(
            "q_ai.inject.campaign.run_campaign",
            side_effect=mock_run_campaign,
        ):
            await execute_inject_step(step, config, overrides)

        assert len(captured_templates) == 1
        assert captured_templates[0].tool_name == "overridden_tool"
        assert captured_templates[0].tool_description == "overridden desc"

    @pytest.mark.asyncio
    async def test_no_matching_templates(self):
        """Returns FAILED when no templates match the technique."""
        step = ChainStep(
            id="step-1",
            name="Inject",
            module="inject",
            technique="description_poisoning",
        )
        config = TargetConfig(inject_model="test-model")

        with patch(
            "q_ai.inject.payloads.loader.load_all_templates",
            return_value=[],
        ):
            output = await execute_inject_step(step, config, {})

        assert output.success is False
        assert "No templates found" in output.error
