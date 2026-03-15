"""Assess MCP Server workflow executor.

Orchestrates audit, proxy, and inject modules to evaluate the security
posture of an MCP server.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from q_ai.audit.adapter import AuditAdapter, AuditResult
from q_ai.core.models import RunStatus
from q_ai.inject.adapter import InjectAdapter
from q_ai.proxy.adapter import ProxyAdapter

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


async def assess_mcp_server(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Assess an MCP server: audit -> proxy (background) + inject.

    Failure mode: best_effort. Audit failure doesn't prevent inject.
    Proxy failure doesn't prevent inject. All failures are recorded
    on their child runs.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict with shape::

            {
                "target_id": str,
                "transport": str,
                "command": str | None,
                "url": str | None,
                "audit": {"checks": list[str] | None},
                "inject": {"model": str, "rounds": int},
                "proxy": {"intercept": bool},
            }
    """
    target = await runner.resolve_target(config["target_id"])

    # Shared transport/connection keys for adapter configs
    connection_base: dict[str, Any] = {
        "transport": config["transport"],
        "command": config.get("command"),
        "url": config.get("url"),
        "target_id": config["target_id"],
    }

    any_failed = False

    # --- Stage 1: Audit ---
    audit_result: AuditResult | None = None
    audit_config = {**connection_base, **config.get("audit", {})}

    try:
        await runner.emit_progress(runner.run_id, "Starting audit scan...")
        audit_result = await AuditAdapter(runner, audit_config).run()
        await runner.emit_progress(
            runner.run_id,
            f"Audit complete: {audit_result.finding_count} findings",
        )
    except Exception:
        logger.exception("Audit stage failed for target %s", target.id)
        any_failed = True
        await runner.emit_progress(runner.run_id, "Audit stage failed, continuing...")

    # --- Stage 2: Proxy (background) + Inject ---
    proxy_adapter: ProxyAdapter | None = None
    proxy_started = False
    proxy_config = {**connection_base, **config.get("proxy", {})}

    try:
        await runner.emit_progress(runner.run_id, "Starting proxy...")
        proxy_adapter = ProxyAdapter(runner, proxy_config)
        await proxy_adapter.start()
        proxy_started = True
        await runner.emit_progress(runner.run_id, "Proxy running in background")
    except Exception:
        logger.exception("Proxy stage failed for target %s", target.id)
        any_failed = True
        await runner.emit_progress(runner.run_id, "Proxy stage failed, continuing without proxy...")

    # --- Stage 2b: Inject (with proxy cleanup guarantee) ---
    try:
        inject_section = config.get("inject", {})
        inject_config: dict[str, Any] = {
            **connection_base,
            **inject_section,
        }

        # Pass audit findings to inject if available
        if audit_result is not None:
            inject_config["audit_findings"] = audit_result.scan_result.findings

        await runner.emit_progress(runner.run_id, "Starting inject campaign...")
        inject_result = await InjectAdapter(runner, inject_config).run()
        await runner.emit_progress(
            runner.run_id,
            f"Inject complete: {inject_result.finding_count} findings",
        )
    except Exception:
        logger.exception("Inject stage failed for target %s", target.id)
        any_failed = True
        await runner.emit_progress(runner.run_id, "Inject stage failed")
    finally:
        # Guarantee proxy cleanup
        if proxy_started and proxy_adapter is not None:
            try:
                await proxy_adapter.stop()
                await runner.emit_progress(runner.run_id, "Proxy stopped")
            except Exception:
                logger.exception("Failed to stop proxy for target %s", target.id)
                any_failed = True

    # --- Terminal status ---
    if any_failed:
        await runner.complete(RunStatus.PARTIAL)
    else:
        await runner.complete(RunStatus.COMPLETED)
