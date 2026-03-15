"""Tests for injection campaign executor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from q_ai.core.llm import NormalizedResponse, ProviderError, ToolCall, UnsupportedCapabilityError
from q_ai.inject.campaign import run_campaign
from q_ai.inject.models import (
    InjectionOutcome,
    InjectionTechnique,
    PayloadTemplate,
)


def _make_template(
    name: str = "test_payload",
    tool_name: str = "get_weather",
    test_query: str = "What's the weather?",
) -> PayloadTemplate:
    """Create a minimal test PayloadTemplate."""
    return PayloadTemplate(
        name=name,
        technique=InjectionTechnique.DESCRIPTION_POISONING,
        description="Test payload",
        tool_name=tool_name,
        tool_description="A test tool",
        tool_params={"city": {"type": "string", "description": "City name"}},
        tool_response="Weather for {city}",
        test_query=test_query,
    )


def _mock_provider(response: NormalizedResponse) -> AsyncMock:
    """Create a mock ProviderClient returning a fixed response."""
    mock_client = AsyncMock()
    mock_client.complete = AsyncMock(return_value=response)
    return mock_client


class TestRunCampaign:
    """Tests for run_campaign with mocked ProviderClient."""

    async def test_campaign_calls_provider_client(self, tmp_path: Path) -> None:
        """Campaign calls client.complete() for each template."""
        response = NormalizedResponse(
            tool_calls=[ToolCall(name="get_weather", arguments={"city": "London"})],
            content="",
            finish_reason="tool_calls",
            raw_response={"id": "r1"},
            model="test-model",
        )
        mock_client = _mock_provider(response)

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template()],
                model="test-model",
                rounds=1,
                output_dir=tmp_path,
            )

        assert len(campaign.results) == 1
        assert campaign.results[0].outcome == InjectionOutcome.FULL_COMPLIANCE
        assert campaign.results[0].payload_name == "test_payload"
        assert campaign.results[0].target_agent == "test-model"
        assert campaign.model == "test-model"
        assert campaign.finished_at is not None
        mock_client.complete.assert_called_once()

        # Verify JSON file was written
        json_files = list(tmp_path.glob("campaign-*.json"))
        assert len(json_files) == 1

    async def test_campaign_passes_tool_spec(self) -> None:
        """ToolSpec is passed to client.complete, not Anthropic ToolParam."""
        from q_ai.core.llm import ToolSpec

        response = NormalizedResponse(
            tool_calls=[ToolCall(name="get_weather", arguments={})],
            raw_response={},
            model="test-model",
        )
        mock_client = _mock_provider(response)

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            await run_campaign(
                templates=[_make_template()],
                model="test-model",
            )

        call_args = mock_client.complete.call_args
        tools = call_args.kwargs["tools"]
        assert len(tools) == 1
        assert isinstance(tools[0], ToolSpec)
        assert tools[0].name == "get_weather"

    async def test_campaign_handles_provider_error(self) -> None:
        """ProviderError results in InjectionOutcome.ERROR."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=ProviderError("Rate limited"))

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template(), _make_template(name="second")],
                model="test-model",
            )

        assert len(campaign.results) == 2
        assert all(r.outcome == InjectionOutcome.ERROR for r in campaign.results)
        evidence = json.loads(campaign.results[0].evidence)
        assert evidence["type"] == "provider_error"

    async def test_campaign_handles_unsupported_capability(self) -> None:
        """UnsupportedCapabilityError results in ERROR with correct evidence type."""
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(side_effect=UnsupportedCapabilityError("No tool support"))

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template()],
                model="ollama/llama3",
            )

        assert campaign.results[0].outcome == InjectionOutcome.ERROR
        evidence = json.loads(campaign.results[0].evidence)
        assert evidence["type"] == "unsupported_capability"

    async def test_campaign_model_string_passthrough(self) -> None:
        """Model string is passed unchanged to client.complete()."""
        response = NormalizedResponse(raw_response={}, model="openai/gpt-4o")
        mock_client = _mock_provider(response)

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            await run_campaign(
                templates=[_make_template()],
                model="openai/gpt-4o",
            )

        call_args = mock_client.complete.call_args
        assert call_args.kwargs["model"] == "openai/gpt-4o"

    async def test_clean_refusal_response(self) -> None:
        """Response with no tool calls results in CLEAN_REFUSAL."""
        response = NormalizedResponse(
            content="I cannot use that tool.",
            finish_reason="stop",
            raw_response={},
            model="test-model",
        )
        mock_client = _mock_provider(response)

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template()],
                model="test-model",
            )

        assert campaign.results[0].outcome == InjectionOutcome.CLEAN_REFUSAL

    async def test_multiple_rounds(self) -> None:
        """Multiple rounds per payload produce multiple results."""
        response = NormalizedResponse(
            content="No thanks.",
            finish_reason="stop",
            raw_response={},
            model="test-model",
        )
        mock_client = _mock_provider(response)

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template()],
                model="test-model",
                rounds=3,
            )

        assert len(campaign.results) == 3

    async def test_fallback_test_query(self) -> None:
        """Template with empty test_query uses fallback."""
        template = _make_template(test_query="")
        response = NormalizedResponse(
            content="Ok",
            finish_reason="stop",
            raw_response={},
            model="test-model",
        )
        mock_client = _mock_provider(response)

        with patch("q_ai.inject.campaign.get_provider_client", return_value=mock_client):
            await run_campaign(templates=[template], model="test-model")

        call_args = mock_client.complete.call_args
        messages = call_args.kwargs["messages"]
        assert "get_weather" in messages[0]["content"]


class TestModelRequired:
    """Verify model parameter is required (no default)."""

    async def test_model_must_be_provided(self) -> None:
        """run_campaign requires an explicit model argument."""
        import inspect

        sig = inspect.signature(run_campaign)
        param = sig.parameters["model"]
        assert param.default is inspect.Parameter.empty
