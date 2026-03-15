"""Tests for LiteLLM provider client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from q_ai.core.llm import (
    NormalizedResponse,
    ProviderError,
    ToolSpec,
    UnsupportedCapabilityError,
)
from q_ai.core.llm_litellm import LiteLLMClient, _tool_spec_to_openai_format


class TestToolSpecToOpenaiFormat:
    """Tests for _tool_spec_to_openai_format()."""

    def test_converts_correctly(self) -> None:
        spec = ToolSpec(
            name="get_weather",
            description="Get weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        result = _tool_spec_to_openai_format(spec)
        assert result["type"] == "function"
        assert result["function"]["name"] == "get_weather"
        assert result["function"]["description"] == "Get weather"
        assert result["function"]["parameters"]["properties"]["city"]["type"] == "string"


class TestLiteLLMClient:
    """Tests for LiteLLMClient.complete()."""

    async def test_complete_with_tool_calls(self) -> None:
        """Mock response with tool_calls returns correct NormalizedResponse."""
        mock_tool_call = MagicMock()
        mock_tool_call.function.name = "get_weather"
        mock_tool_call.function.arguments = json.dumps({"city": "London"})

        mock_message = MagicMock()
        mock_message.tool_calls = [mock_tool_call]
        mock_message.content = ""

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "tool_calls"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "resp-1"}

        with patch("q_ai.core.llm_litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            client = LiteLLMClient()
            result = await client.complete(
                model="anthropic/claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": "test"}],
                tools=[ToolSpec(name="get_weather", description="Get weather", parameters={})],
            )

        assert isinstance(result, NormalizedResponse)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == {"city": "London"}
        assert result.finish_reason == "tool_calls"
        assert result.content == ""

    async def test_complete_no_tool_calls(self) -> None:
        """Mock response with content only returns empty tool_calls."""
        mock_message = MagicMock()
        mock_message.tool_calls = None
        mock_message.content = "I cannot use that tool."

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {"id": "resp-1"}

        with patch("q_ai.core.llm_litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            client = LiteLLMClient()
            result = await client.complete(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": "test"}],
                tools=[],
            )

        assert result.tool_calls == []
        assert result.content == "I cannot use that tool."
        assert result.finish_reason == "stop"

    async def test_complete_provider_error(self) -> None:
        """acompletion exception raises ProviderError."""
        with patch("q_ai.core.llm_litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.side_effect = Exception("Rate limited")
            client = LiteLLMClient()
            with pytest.raises(ProviderError, match="Provider anthropic error"):
                await client.complete(
                    model="anthropic/claude-sonnet-4-20250514",
                    messages=[{"role": "user", "content": "test"}],
                    tools=[],
                )

    async def test_complete_unsupported_capability(self) -> None:
        """Tool support error raises UnsupportedCapabilityError."""
        with patch("q_ai.core.llm_litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.side_effect = Exception("This model does not support tools")
            client = LiteLLMClient()
            with pytest.raises(UnsupportedCapabilityError, match="does not support tool calling"):
                await client.complete(
                    model="ollama/llama3",
                    messages=[{"role": "user", "content": "test"}],
                    tools=[ToolSpec(name="t", description="t", parameters={})],
                )

    async def test_complete_string_arguments_parsed(self) -> None:
        """Tool call arguments arriving as strings are parsed through json.loads."""
        mock_tool_call = MagicMock()
        mock_tool_call.function.name = "get_data"
        mock_tool_call.function.arguments = '{"key": "value"}'

        mock_message = MagicMock()
        mock_message.tool_calls = [mock_tool_call]
        mock_message.content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "tool_calls"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {}

        with patch("q_ai.core.llm_litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            client = LiteLLMClient()
            result = await client.complete(
                model="openai/gpt-4o",
                messages=[{"role": "user", "content": "test"}],
                tools=[ToolSpec(name="get_data", description="d", parameters={})],
            )

        assert result.tool_calls[0].arguments == {"key": "value"}
        assert result.content == ""

    async def test_complete_malformed_tool_arguments(self) -> None:
        """Malformed JSON in tool arguments raises ProviderError."""
        mock_tool_call = MagicMock()
        mock_tool_call.function.name = "get_data"
        mock_tool_call.function.arguments = "not valid json{{"

        mock_message = MagicMock()
        mock_message.tool_calls = [mock_tool_call]
        mock_message.content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "tool_calls"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model_dump.return_value = {}

        with patch("q_ai.core.llm_litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            client = LiteLLMClient()
            with pytest.raises(ProviderError, match="Malformed tool call arguments"):
                await client.complete(
                    model="openai/gpt-4o",
                    messages=[{"role": "user", "content": "test"}],
                    tools=[ToolSpec(name="get_data", description="d", parameters={})],
                )
