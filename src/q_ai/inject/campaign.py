"""Campaign executor for injection testing.

Loads payloads, presents poisoned tools to an AI model via a provider-agnostic
client, captures structured results, and writes campaign JSON.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from q_ai.core.llm import (
    ProviderError,
    UnsupportedCapabilityError,
    get_provider_client,
    serialize_evidence,
    tool_spec_from_template,
)
from q_ai.inject.models import (
    Campaign,
    InjectionOutcome,
    InjectionResult,
    PayloadTemplate,
)
from q_ai.inject.scoring import score_response

logger = logging.getLogger(__name__)


async def run_campaign(
    templates: list[PayloadTemplate],
    model: str,
    rounds: int = 1,
    output_dir: Path | None = None,
) -> Campaign:
    """Execute an injection campaign against an AI model.

    For each template, converts it to a provider-agnostic ToolSpec,
    sends the test query via the configured provider, scores the response,
    and records the result.

    Args:
        templates: Payload templates to test.
        model: Model string in provider/model format (e.g. anthropic/claude-sonnet-4-20250514).
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

    client = get_provider_client(model)

    for template in templates:
        tool_spec = tool_spec_from_template(template)
        test_query = template.test_query or f"Use the {template.tool_name} tool."

        for round_num in range(rounds):
            logger.info(
                "Testing payload '%s' (round %d/%d)",
                template.name,
                round_num + 1,
                rounds,
            )
            try:
                response = await client.complete(
                    model=model,
                    messages=[{"role": "user", "content": test_query}],
                    tools=[tool_spec],
                )

                outcome = score_response(response)
                evidence = serialize_evidence(response)

            except UnsupportedCapabilityError as exc:
                logger.warning("Unsupported capability for payload '%s': %s", template.name, exc)
                outcome = InjectionOutcome.ERROR
                evidence = json.dumps({"error": str(exc), "type": "unsupported_capability"})

            except ProviderError as exc:
                logger.warning("Provider error for payload '%s': %s", template.name, exc)
                outcome = InjectionOutcome.ERROR
                evidence = json.dumps({"error": str(exc), "type": "provider_error"})

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
