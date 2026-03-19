"""Quick action executors for single-module operations.

Each executor wraps a single module adapter (audit, proxy, inject)
with the WorkflowRunner lifecycle so the operation gets a proper
parent run and child run visible in the run results view.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from q_ai.audit.adapter import AuditAdapter
from q_ai.core.models import RunStatus
from q_ai.inject.adapter import InjectAdapter
from q_ai.proxy.adapter import ProxyAdapter

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


async def quick_scan(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Run a standalone audit scan.

    Args:
        runner: WorkflowRunner managing the parent run.
        config: Configuration dict with transport, command/url, target_id.
    """
    await runner.resolve_target(config["target_id"])
    await runner.emit_progress(runner.run_id, "Starting audit scan...")

    audit_config: dict[str, Any] = {
        "transport": config["transport"],
        "command": config.get("command"),
        "url": config.get("url"),
        "target_id": config["target_id"],
        "checks": None,
    }

    try:
        result = await AuditAdapter(runner, audit_config).run()
        await runner.emit_progress(
            runner.run_id,
            f"Scan complete: {result.finding_count} findings",
        )
        await runner.complete(RunStatus.COMPLETED)
    except Exception:
        logger.exception("Quick scan failed")
        await runner.fail(error="Scan failed")


async def quick_intercept(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Run a standalone proxy intercept session.

    Starts the proxy and waits for user action to stop it.

    Args:
        runner: WorkflowRunner managing the parent run.
        config: Configuration dict with transport, command/url, target_id.
    """
    await runner.resolve_target(config["target_id"])
    await runner.emit_progress(runner.run_id, "Starting proxy...")

    proxy_config: dict[str, Any] = {
        "transport": config["transport"],
        "command": config.get("command"),
        "url": config.get("url"),
        "target_id": config["target_id"],
        "intercept": False,
    }

    try:
        adapter = ProxyAdapter(runner, proxy_config)
        await adapter.start()
        await runner.emit_progress(runner.run_id, "Proxy running — stop when ready")
        await runner.wait_for_user("Click Resume to stop the proxy and save the session")
        await adapter.stop()
        await runner.emit_progress(runner.run_id, "Proxy stopped")
        await runner.complete(RunStatus.COMPLETED)
    except Exception:
        logger.exception("Quick intercept failed")
        await runner.fail(error="Intercept failed")


async def quick_campaign(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Run a standalone inject campaign.

    Args:
        runner: WorkflowRunner managing the parent run.
        config: Configuration dict with transport, command/url, target_id,
            model, rounds.
    """
    await runner.resolve_target(config["target_id"])
    await runner.emit_progress(runner.run_id, "Starting inject campaign...")

    connection_base: dict[str, Any] = {
        "transport": config["transport"],
        "command": config.get("command"),
        "url": config.get("url"),
        "target_id": config["target_id"],
    }
    inject_config: dict[str, Any] = {
        **connection_base,
        "model": config["model"],
        "rounds": config.get("rounds", 1),
    }

    try:
        result = await InjectAdapter(runner, inject_config).run()
        await runner.emit_progress(
            runner.run_id,
            f"Campaign complete: {result.finding_count} findings",
        )
        await runner.complete(RunStatus.COMPLETED)
    except Exception:
        logger.exception("Quick campaign failed")
        await runner.fail(error="Campaign failed")
