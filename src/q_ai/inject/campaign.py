"""Campaign executor for injection testing.

Loads payloads, presents poisoned tools to an AI model via the Anthropic
tool-use API, captures structured results, and writes campaign JSON.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from anthropic import APIError, AsyncAnthropic
from anthropic.types import ToolParam

from q_ai.inject.models import (
    Campaign,
    InjectionOutcome,
    InjectionResult,
    PayloadTemplate,
)
from q_ai.inject.scoring import score_response

logger = logging.getLogger(__name__)


def _template_to_tool(template: PayloadTemplate) -> dict[str, Any]:
    """Convert a PayloadTemplate to an Anthropic tool definition.

    Args:
        template: Payload template with tool metadata.

    Returns:
        Tool definition dict suitable for the Anthropic Messages API.
    """
    properties: dict[str, dict[str, str]] = {}
    for name, info in template.tool_params.items():
        prop: dict[str, str] = {"type": info.get("type", "string")}
        if desc := info.get("description", ""):
            prop["description"] = desc
        properties[name] = prop

    return {
        "name": template.tool_name,
        "description": template.tool_description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(template.tool_params.keys()),
        },
    }


def _serialize_content(content: Any) -> str:
    """Serialize Anthropic response content blocks to a JSON string.

    Handles both SDK message objects and raw dicts.

    Args:
        content: The ``content`` attribute from an Anthropic Messages response.

    Returns:
        JSON string of the serialized content blocks.
    """
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        else:
            # SDK object — convert via model_dump if available, else vars
            if hasattr(block, "model_dump"):
                blocks.append(block.model_dump())
            else:
                try:
                    blocks.append(vars(block))
                except TypeError:
                    blocks.append({"type": getattr(block, "type", "unknown")})
    return json.dumps(blocks, indent=2, default=str)


async def run_campaign(
    templates: list[PayloadTemplate],
    model: str,
    rounds: int = 1,
    output_dir: Path | None = None,
) -> Campaign:
    """Execute an injection campaign against an AI model.

    For each template, converts it to an Anthropic tool definition,
    sends the test query via the Messages API, scores the response,
    and records the result.

    Args:
        templates: Payload templates to test.
        model: Anthropic model ID.
        rounds: Number of attempts per payload.
        output_dir: Directory to write campaign JSON. If None, no file is written.

    Returns:
        Completed Campaign object with all results.
    """
    campaign_id = f"campaign-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    campaign = Campaign(
        id=campaign_id,
        name="inject-campaign",
        model=model,
    )

    client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env

    for template in templates:
        tool_def = _template_to_tool(template)
        test_query = template.test_query or f"Use the {template.tool_name} tool."

        for round_num in range(rounds):
            logger.info(
                "Testing payload '%s' (round %d/%d)",
                template.name,
                round_num + 1,
                rounds,
            )
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=1024,
                    messages=[{"role": "user", "content": test_query}],
                    tools=[cast(ToolParam, tool_def)],
                )

                outcome = score_response(response.content)
                evidence = _serialize_content(response.content)

            except APIError as exc:
                logger.warning("API error for payload '%s': %s", template.name, exc)
                outcome = InjectionOutcome.ERROR
                evidence = json.dumps({"error": str(exc)})

            result = InjectionResult(
                payload_name=template.name,
                technique=template.technique.value,
                outcome=outcome,
                evidence=evidence,
                target_agent=model,
            )
            campaign.results.append(result)

    campaign.finished_at = datetime.now(UTC)

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{campaign_id}.json"
        output_path.write_text(campaign.to_json(), encoding="utf-8")
        logger.info("Campaign results written to %s", output_path)

    return campaign
