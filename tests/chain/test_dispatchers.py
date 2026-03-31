"""Tests for the chain step dispatchers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from q_ai.chain.executor import (
    execute_audit_step,
    execute_cxp_step,
    execute_inject_step,
    execute_ipi_step,
    execute_rxp_step,
)
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


class TestExecuteIPIStepErrors:
    """Test IPI dispatcher error handling."""

    @pytest.mark.asyncio
    async def test_missing_output_dir(self):
        """Returns FAILED when no IPI output dir is configured."""
        step = ChainStep(id="s1", name="IPI", module="ipi", technique="pdf")
        config = TargetConfig()

        output = await execute_ipi_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "ipi_output_dir" in output.error

    @pytest.mark.asyncio
    async def test_generation_success(self):
        """Returns SUCCESS when payloads are generated."""
        step = ChainStep(id="s1", name="IPI", module="ipi", technique="pdf")
        config = TargetConfig(
            ipi_callback_url="http://localhost:8080",
            ipi_output_dir="/tmp/ipi-test",
            ipi_format="pdf",
        )

        from dataclasses import dataclass, field

        @dataclass
        class MockCampaign:
            technique: str = "white_ink"
            output_path: str = "/tmp/ipi-test/out.pdf"
            format: str = "pdf"

        @dataclass
        class MockGenerateResult:
            campaigns: list = field(default_factory=lambda: [MockCampaign()])
            skipped: int = 0
            errors: list = field(default_factory=list)

        from q_ai.ipi.models import Technique

        with (
            patch(
                "q_ai.ipi.generate_service.generate_documents",
                return_value=MockGenerateResult(),
            ),
            patch(
                "q_ai.ipi.generators.get_techniques_for_format",
                return_value=[Technique.WHITE_INK],
            ),
        ):
            output = await execute_ipi_step(step, config, {})

        assert output.success is True
        assert output.status == StepStatus.SUCCESS
        assert output.generate_result is not None
        assert output.artifacts["payload_count"] == "1"

    @pytest.mark.asyncio
    async def test_generation_failure(self):
        """Returns FAILED when generate_documents raises."""
        step = ChainStep(id="s1", name="IPI", module="ipi", technique="pdf")
        config = TargetConfig(ipi_output_dir="/tmp/ipi-test")

        from q_ai.ipi.models import Technique

        with (
            patch(
                "q_ai.ipi.generate_service.generate_documents",
                side_effect=RuntimeError("generation boom"),
            ),
            patch(
                "q_ai.ipi.generators.get_techniques_for_format",
                return_value=[Technique.WHITE_INK],
            ),
        ):
            output = await execute_ipi_step(step, config, {})

        assert output.success is False
        assert "generation boom" in output.error


class TestExecuteCXPStepErrors:
    """Test CXP dispatcher error handling."""

    @pytest.mark.asyncio
    async def test_missing_format_id(self):
        """Returns FAILED when no CXP format ID is configured."""
        step = ChainStep(id="s1", name="CXP", module="cxp", technique="cursorrules")
        config = TargetConfig()

        output = await execute_cxp_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "cxp_format_id" in output.error

    @pytest.mark.asyncio
    async def test_missing_output_dir(self):
        """Returns FAILED when no CXP output dir is configured."""
        step = ChainStep(id="s1", name="CXP", module="cxp", technique="cursorrules")
        config = TargetConfig(cxp_format_id="cursorrules")

        output = await execute_cxp_step(step, config, {})

        assert output.success is False
        assert "cxp_output_dir" in output.error

    @pytest.mark.asyncio
    async def test_build_success(self, tmp_path):
        """Returns SUCCESS when build succeeds."""
        step = ChainStep(id="s1", name="CXP", module="cxp", technique="cursorrules")
        config = TargetConfig(
            cxp_format_id="cursorrules",
            cxp_output_dir=str(tmp_path),
        )

        from dataclasses import dataclass

        @dataclass
        class MockBuildResult:
            repo_dir: str = "/tmp/repo"
            rules_inserted: list = None
            format_id: str = "cursorrules"

            def __post_init__(self):
                if self.rules_inserted is None:
                    self.rules_inserted = ["rule-1"]

        with (
            patch("q_ai.cxp.builder.build", return_value=MockBuildResult()),
            patch("q_ai.cxp.catalog.get_rule", return_value=None),
        ):
            output = await execute_cxp_step(step, config, {})

        assert output.success is True
        assert output.build_result is not None
        assert output.artifacts["format_id"] == "cursorrules"


class TestExecuteRXPStepErrors:
    """Test RXP dispatcher error handling."""

    @pytest.mark.asyncio
    async def test_missing_model_id(self):
        """Returns FAILED when no RXP model ID is configured."""
        step = ChainStep(id="s1", name="RXP", module="rxp", technique="minilm-l6")
        config = TargetConfig()

        output = await execute_rxp_step(step, config, {})

        assert output.success is False
        assert output.status == StepStatus.FAILED
        assert "rxp_model_id" in output.error

    @pytest.mark.asyncio
    async def test_missing_profile(self):
        """Returns FAILED when no profile_id is provided."""
        step = ChainStep(id="s1", name="RXP", module="rxp", technique="minilm-l6")
        config = TargetConfig(rxp_model_id="minilm-l6")

        output = await execute_rxp_step(step, config, {})

        assert output.success is False
        assert "profile_id" in output.error

    @pytest.mark.asyncio
    async def test_validation_success(self):
        """Returns SUCCESS when validation finds poison retrieval."""
        import sys
        import types
        from dataclasses import dataclass, field

        step = ChainStep(id="s1", name="RXP", module="rxp", technique="minilm-l6")
        config = TargetConfig(rxp_model_id="minilm-l6", rxp_profile_id="test-profile")

        @dataclass
        class MockProfile:
            queries: list = field(default_factory=lambda: ["test query"])

        @dataclass
        class MockValidationResult:
            model_id: str = "minilm-l6"
            total_queries: int = 1
            poison_retrievals: int = 1
            retrieval_rate: float = 1.0
            mean_poison_rank: float = 1.0
            query_results: list = field(default_factory=list)

        # Stub modules that require chromadb so deferred imports succeed
        mock_profiles = types.ModuleType("q_ai.rxp.profiles")
        mock_profiles.get_profile = lambda pid: MockProfile()
        mock_profiles.load_corpus = lambda p: []
        mock_profiles.load_poison = lambda p: []

        mock_validator = types.ModuleType("q_ai.rxp.validator")
        mock_validator.validate_retrieval = lambda *a, **kw: MockValidationResult()

        with patch.dict(
            sys.modules,
            {
                "q_ai.rxp.profiles": mock_profiles,
                "q_ai.rxp.validator": mock_validator,
            },
        ):
            output = await execute_rxp_step(step, config, {})

        assert output.success is True
        assert output.status == StepStatus.SUCCESS
        assert output.validation_result is not None
        assert output.artifacts["retrieval_rate"] == "100%"
        assert output.artifacts["model_id"] == "minilm-l6"
