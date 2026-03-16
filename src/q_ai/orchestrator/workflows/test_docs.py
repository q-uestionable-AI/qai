"""Test Document Ingestion workflow executor.

Generates IPI payloads for document ingestion pipelines, optionally
pre-validating retrieval rank with RXP before deployment.

Config shape::

    {
        "target_id": str,
        "callback_url": str,
        "output_dir": str,
        "format": str,
        "payload_style": str,       # default "obvious"
        "payload_type": str,        # default "callback"
        "base_name": str,           # default "report"
        "rxp_enabled": bool,        # default False
        "rxp": {                    # only read if rxp_enabled is True
            "model_id": str,
            "profile_id": str | None,
        },
    }
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from q_ai.core.models import RunStatus
from q_ai.ipi.adapter import IPIAdapter
from q_ai.rxp.adapter import RXPAdapter

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


async def test_document_ingestion(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Orchestrate IPI payload generation with optional RXP pre-validation.

    Error policy: best_effort. RXP failure does not block IPI.
    IPI failure marks the run PARTIAL.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict — see module docstring for shape.
    """
    any_failed = False

    # --- Optional stage: RXP pre-validation ---
    if config.get("rxp_enabled"):
        rxp_config = {**config["rxp"], "target_id": config["target_id"]}
        try:
            await runner.emit_progress(runner.run_id, "Starting RXP pre-validation...")
            await RXPAdapter(runner, rxp_config).run()
            await runner.emit_progress(runner.run_id, "RXP pre-validation complete")
        except Exception:
            logger.exception("RXP stage failed for target %s", config["target_id"])
            any_failed = True
            await runner.emit_progress(runner.run_id, "RXP stage failed, continuing...")

    # --- Main stage: IPI payload generation ---
    ipi_config = {
        "target_id": config["target_id"],
        "callback_url": config["callback_url"],
        "output_dir": config["output_dir"],
        "format": config["format"],
        "payload_style": config.get("payload_style", "obvious"),
        "payload_type": config.get("payload_type", "callback"),
        "base_name": config.get("base_name", "report"),
    }
    try:
        await runner.emit_progress(runner.run_id, "Starting IPI payload generation...")
        await IPIAdapter(runner, ipi_config).run()
        await runner.emit_progress(runner.run_id, "IPI payload generation complete")
    except Exception:
        logger.exception("IPI stage failed for target %s", config["target_id"])
        any_failed = True
        await runner.emit_progress(runner.run_id, "IPI stage failed")

    # --- Terminal status ---
    if any_failed:
        await runner.complete(RunStatus.PARTIAL)
    else:
        await runner.complete(RunStatus.COMPLETED)
