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

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from q_ai.core.models import RunStatus
from q_ai.ipi.adapter import IPIAdapter, RetrievalGate
from q_ai.ipi.callback_state import is_pid_alive
from q_ai.rxp.adapter import RXPAdapter, RXPAdapterResult
from q_ai.services.managed_listener import (
    ListenerState,
    ManagedListenerConflictError,
    ManagedListenerStartupError,
    start_managed_listener,
)

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})


def _callback_url_requires_tunnel(url: str) -> bool:
    """Return True when ``url`` points at a non-localhost host.

    Used as the heuristic for "does this workflow need a publicly
    reachable tunnel?" per the RFC. Any parse failure is treated as
    "no tunnel needed" — we err toward leaving the user's explicit
    choice alone.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host not in _LOCAL_HOSTS


def _substitute_public_url(callback_url: str, public_url: str) -> str:
    """Return ``callback_url`` with its scheme+host+port replaced by
    those of ``public_url``. Path/query/fragment are preserved so
    existing ``/callback`` (or similar) route paths flow through."""
    parsed = urlparse(callback_url)
    pub = urlparse(public_url)
    return urlunparse(
        (
            pub.scheme,
            pub.netloc,
            parsed.path or "",
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


async def _ensure_tunnel_for_workflow(  # noqa: PLR0911 — early-return ladder is clearer than nested conditionals here
    runner: WorkflowRunner, callback_url: str
) -> str | None:
    """Ensure a tunneled listener is active and return its public URL.

    Returns ``None`` when no tunnel is required, the runner has no
    ``app_state`` reference (CLI/test usage), or auto-start fails. Per
    RFC Decision 1, this function never calls ``stop_managed_listener``:
    the listener is a global resource the user manages from the IPI
    tab.
    """
    if not _callback_url_requires_tunnel(callback_url):
        return None

    app_state = runner.app_state
    if app_state is None:
        return None

    registry = getattr(app_state, "managed_listeners", None)
    foreign = getattr(app_state, "foreign_listener", None)
    qai_dir = getattr(app_state, "qai_dir", None)

    # Reuse an existing managed listener before considering a spawn — but only
    # if its PID is actually live. The adopted-listener poller catches dead
    # handles within ~5s, and this per-call check closes the window between
    # scans so a short-lived external death doesn't route callbacks to a
    # dead tunnel.
    if registry is not None:
        for handle in registry.values():
            if handle.state not in (ListenerState.RUNNING, ListenerState.ADOPTED):
                continue
            if not is_pid_alive(handle.pid):
                continue
            return str(handle.public_url)

    # Foreign listener is also acceptable — it holds the single-listener
    # slot and already provides a public URL. Foreign records are never
    # refreshed after lifespan so the liveness check matters more here.
    if foreign is not None and is_pid_alive(foreign.pid):
        return str(foreign.public_url)

    if registry is None:
        return None

    try:
        handle = await asyncio.to_thread(start_managed_listener, registry, qai_dir=qai_dir)
    except (ManagedListenerConflictError, ManagedListenerStartupError) as err:
        logger.warning(
            "Auto-start of managed listener for test_docs workflow failed: %s",
            err.detail,
        )
        await runner.emit_progress(
            runner.run_id,
            f"Tunnel auto-start failed ({err.detail}); using original callback URL",
        )
        return None
    return str(handle.public_url)


def _build_retrieval_gate(rxp_result: RXPAdapterResult, config: dict[str, Any]) -> RetrievalGate:
    """Build a RetrievalGate from RXP per-query results.

    Args:
        rxp_result: Completed RXP adapter result with per-query detail.
        config: Workflow config (may contain ``rxp_retrieval_threshold``).

    Returns:
        RetrievalGate with per-query viability and configured threshold.
    """
    query_viability = {qr.query: qr.poison_retrieved for qr in rxp_result.result.query_results}
    threshold = config.get("rxp_retrieval_threshold", 0.0)
    return RetrievalGate(
        retrieval_rate=rxp_result.retrieval_rate,
        query_viability=query_viability,
        threshold=threshold,
    )


async def test_document_ingestion(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Orchestrate IPI payload generation with optional RXP pre-validation.

    Error policy: best_effort. RXP failure does not block IPI.
    IPI failure marks the run PARTIAL.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict — see module docstring for shape.
    """
    any_failed = False
    rxp_result: RXPAdapterResult | None = None

    # --- Optional stage: RXP pre-validation ---
    if config.get("rxp_enabled"):
        rxp_config = {**config["rxp"], "target_id": config["target_id"]}
        try:
            await runner.emit_progress(runner.run_id, "Starting RXP pre-validation...")
            rxp_result = await RXPAdapter(runner, rxp_config).run()
            await runner.emit_progress(runner.run_id, "RXP pre-validation complete")
        except Exception:
            logger.exception("RXP stage failed for target %s", config["target_id"])
            any_failed = True
            await runner.emit_progress(runner.run_id, "RXP stage failed, continuing...")

    # --- Ensure a tunnel if the target demands remote reachability. ---
    effective_callback_url = config["callback_url"]
    tunnel_public_url = await _ensure_tunnel_for_workflow(runner, effective_callback_url)
    if tunnel_public_url is not None:
        effective_callback_url = _substitute_public_url(effective_callback_url, tunnel_public_url)
        await runner.emit_progress(
            runner.run_id,
            f"Tunneled listener active; callbacks route through {tunnel_public_url}",
        )

    # --- Main stage: IPI payload generation ---
    ipi_config: dict[str, Any] = {
        "target_id": config["target_id"],
        "callback_url": effective_callback_url,
        "output_dir": config["output_dir"],
        "format": config["format"],
        "payload_style": config.get("payload_style", "obvious"),
        "payload_type": config.get("payload_type", "callback"),
        "base_name": config.get("base_name", "report"),
    }

    if rxp_result is not None:
        gate = _build_retrieval_gate(rxp_result, config)
        ipi_config["retrieval_gate"] = gate
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
