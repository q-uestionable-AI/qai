"""Tests for injection campaign executor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.inject.campaign import (
    _serialize_content,
    _template_to_tool,
    run_campaign,
)
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


class TestTemplateToTool:
    """Tests for _template_to_tool conversion."""

    def test_basic_conversion(self) -> None:
        template = _make_template()
        tool = _template_to_tool(template)
        assert tool["name"] == "get_weather"
        assert tool["description"] == "A test tool"
        assert tool["input_schema"]["type"] == "object"
        assert "city" in tool["input_schema"]["properties"]
        assert tool["input_schema"]["required"] == ["city"]

    def test_no_params(self) -> None:
        template = _make_template()
        template.tool_params = {}
        tool = _template_to_tool(template)
        assert tool["input_schema"]["properties"] == {}
        assert tool["input_schema"]["required"] == []

    def test_multiple_params(self) -> None:
        template = _make_template()
        template.tool_params = {
            "city": {"type": "string", "description": "City"},
            "units": {"type": "string", "description": "Units"},
        }
        tool = _template_to_tool(template)
        assert len(tool["input_schema"]["properties"]) == 2
        assert "city" in tool["input_schema"]["properties"]
        assert "units" in tool["input_schema"]["properties"]


class TestSerializeContent:
    """Tests for _serialize_content helper."""

    def test_dict_blocks(self) -> None:
        blocks = [{"type": "text", "text": "hello"}]
        result = json.loads(_serialize_content(blocks))
        assert result == [{"type": "text", "text": "hello"}]

    def test_sdk_objects_with_model_dump(self) -> None:
        mock = MagicMock()
        mock.model_dump.return_value = {"type": "text", "text": "hello"}
        result = json.loads(_serialize_content([mock]))
        assert result == [{"type": "text", "text": "hello"}]


class TestRunCampaign:
    """Tests for run_campaign with mocked Anthropic client."""

    async def test_successful_campaign(self, tmp_path: Path) -> None:
        """Campaign with tool_use response records result and writes JSON."""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                type="tool_use",
                model_dump=MagicMock(
                    return_value={
                        "type": "tool_use",
                        "id": "t1",
                        "name": "get_weather",
                        "input": {"city": "London"},
                    }
                ),
            ),
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("q_ai.inject.campaign.AsyncAnthropic", return_value=mock_client):
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

        # Verify JSON file was written
        json_files = list(tmp_path.glob("campaign-*.json"))
        assert len(json_files) == 1
        data = json.loads(json_files[0].read_text())
        assert data["model"] == "test-model"
        assert len(data["results"]) == 1

    async def test_clean_refusal_response(self) -> None:
        """Response with only text results in CLEAN_REFUSAL."""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(type="text", text="I cannot use that tool."),
        ]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("q_ai.inject.campaign.AsyncAnthropic", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template()],
                model="test-model",
            )

        assert len(campaign.results) == 1
        assert campaign.results[0].outcome == InjectionOutcome.CLEAN_REFUSAL

    async def test_api_error_records_error_outcome(self) -> None:
        """API error records ERROR outcome, continues campaign."""
        from anthropic import APIStatusError

        mock_client = AsyncMock()
        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {}
        mock_client.messages.create = AsyncMock(
            side_effect=APIStatusError(
                message="Rate limited",
                response=error_response,
                body={"error": {"message": "Rate limited"}},
            )
        )

        with patch("q_ai.inject.campaign.AsyncAnthropic", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template(), _make_template(name="second_payload")],
                model="test-model",
            )

        # Both payloads should have results (campaign continues on error)
        assert len(campaign.results) == 2
        assert all(r.outcome == InjectionOutcome.ERROR for r in campaign.results)
        assert "Rate limited" in campaign.results[0].evidence

    async def test_multiple_rounds(self) -> None:
        """Multiple rounds per payload produce multiple results."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="No thanks.")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("q_ai.inject.campaign.AsyncAnthropic", return_value=mock_client):
            campaign = await run_campaign(
                templates=[_make_template()],
                model="test-model",
                rounds=3,
            )

        assert len(campaign.results) == 3

    async def test_fallback_test_query(self) -> None:
        """Template with empty test_query uses fallback."""
        template = _make_template(test_query="")
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Ok")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("q_ai.inject.campaign.AsyncAnthropic", return_value=mock_client):
            await run_campaign(templates=[template], model="test-model")

        # Verify the fallback query was used
        call_args = mock_client.messages.create.call_args
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
