"""LiteLLM implementation of ProviderClient.

This is the only file that imports litellm. If litellm ever needs
replacing, only this file changes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from litellm import acompletion  # type: ignore[import-untyped]

from q_ai.core.llm import (
    NormalizedResponse,
    ProviderError,
    ToolCall,
    ToolSpec,
    UnsupportedCapabilityError,
    parse_model_string,
)

logger = logging.getLogger(__name__)

# litellm error messages indicating no tool support
_NO_TOOL_SUPPORT_SIGNALS = [
    "does not support tools",
    "does not support function",
    "tool_choice is not supported",
    "tools is not supported",
]


def _tool_spec_to_openai_format(tool: ToolSpec) -> dict[str, Any]:
    """Convert a ToolSpec to OpenAI-compatible tool format.

    Args:
        tool: Provider-agnostic tool specification.

    Returns:
        Dict in OpenAI tool format for litellm.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


class LiteLLMClient:
    """ProviderClient implementation using litellm.

    Handles all providers via litellm's model-string routing.
    Normalizes responses to NormalizedResponse.
    """

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_tokens: int = 1024,
    ) -> NormalizedResponse:
        """Call litellm.acompletion and normalize the response.

        Args:
            model: Model string (provider/model-id or bare).
            messages: Chat messages.
            tools: Tool specifications.
            max_tokens: Maximum tokens in response.

        Returns:
            Normalized response from the provider.

        Raises:
            UnsupportedCapabilityError: If the model doesn't support tool calling.
            ProviderError: For other provider errors.
        """
        provider, _model_id = parse_model_string(model)

        openai_tools = [_tool_spec_to_openai_format(t) for t in tools] if tools else None

        try:
            # Security: Add timeout to prevent external provider calls from hanging indefinitely
            response = await acompletion(  # type: ignore[no-untyped-call]
                model=model,
                messages=messages,
                tools=openai_tools,
                max_tokens=max_tokens,
                timeout=60.0,
            )
        except Exception as exc:
            error_msg = str(exc).lower()
            if any(signal in error_msg for signal in _NO_TOOL_SUPPORT_SIGNALS):
                raise UnsupportedCapabilityError(
                    f"Model {model} does not support tool calling: {exc}"
                ) from exc
            raise ProviderError(f"Provider {provider} error: {exc}") from exc

        # Extract response data
        choice = response.choices[0]  # type: ignore[union-attr]
        message = choice.message  # type: ignore[union-attr]

        # Normalize tool calls
        normalized_tool_calls: list[ToolCall] = []
        if message.tool_calls:  # type: ignore[union-attr]
            for tc in message.tool_calls:  # type: ignore[union-attr]
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError as json_exc:
                        raise ProviderError(
                            f"Malformed tool call arguments from {model}: {json_exc}"
                        ) from json_exc
                normalized_tool_calls.append(ToolCall(name=tc.function.name, arguments=args))

        # Normalize content
        content = message.content or ""  # type: ignore[union-attr]

        # Build raw_response for evidence
        raw: Any = (
            response.model_dump()  # type: ignore[union-attr]
            if hasattr(response, "model_dump")
            else response
        )

        return NormalizedResponse(
            tool_calls=normalized_tool_calls,
            content=content,
            finish_reason=choice.finish_reason,  # type: ignore[union-attr]
            raw_response=raw,
            model=model,
        )
