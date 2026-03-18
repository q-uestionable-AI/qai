"""Adapter for running the proxy pipeline through the orchestrator.

Wraps the proxy pipeline, managing transport setup, background task
lifecycle, and session persistence. Does not use the TUI.
"""

from __future__ import annotations

import asyncio
import shlex
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from q_ai.core.models import RunStatus
from q_ai.mcp.models import Transport
from q_ai.proxy.intercept import InterceptEngine
from q_ai.proxy.mapper import persist_session
from q_ai.proxy.models import InterceptMode
from q_ai.proxy.pipeline import PipelineSession, run_pipeline
from q_ai.proxy.session_store import SessionStore

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner


@dataclass
class ProxyResult:
    """Result from a proxy adapter run."""

    run_id: str
    message_count: int
    duration_seconds: float


def _parse_transport(transport_str: str) -> Transport:
    """Convert a transport string to a Transport enum value.

    Args:
        transport_str: Transport type string.

    Returns:
        Matching Transport enum value.

    Raises:
        ValueError: If the transport string is unknown.
    """
    mapping = {
        "stdio": Transport.STDIO,
        "sse": Transport.SSE,
        "streamable-http": Transport.STREAMABLE_HTTP,
    }
    result = mapping.get(transport_str)
    if result is None:
        raise ValueError(f"Unknown transport: {transport_str}")
    return result


def _create_adapters(config: dict[str, Any]) -> tuple[Any, Any]:
    """Create client and server transport adapters from config.

    Returns concrete adapter instances that implement TransportAdapter
    and support async context management. The return type is Any because
    the Protocol does not include __aenter__/__aexit__.

    Args:
        config: Adapter configuration dict with transport, command, url keys.

    Returns:
        Tuple of (client_adapter, server_adapter).

    Raises:
        ValueError: If required config keys are missing.
    """
    transport = config["transport"]

    if transport == "stdio":
        from q_ai.proxy.adapters.stdio import StdioClientAdapter, StdioServerAdapter

        command = config.get("command")
        if not command:
            raise ValueError("'command' is required for stdio transport")
        parts = shlex.split(command)
        return StdioClientAdapter(), StdioServerAdapter(command=parts[0], args=parts[1:])

    if transport == "sse":
        from q_ai.proxy.adapters.sse import SseServerAdapter
        from q_ai.proxy.adapters.stdio import StdioClientAdapter

        url = config.get("url")
        if not url:
            raise ValueError("'url' is required for SSE transport")
        return StdioClientAdapter(), SseServerAdapter(url=url)

    if transport == "streamable-http":
        from q_ai.proxy.adapters.stdio import StdioClientAdapter
        from q_ai.proxy.adapters.streamable_http import StreamableHttpServerAdapter

        url = config.get("url")
        if not url:
            raise ValueError("'url' is required for streamable-http transport")
        return StdioClientAdapter(), StreamableHttpServerAdapter(url=url)

    raise ValueError(f"Unknown transport: {transport}")


class ProxyAdapter:
    """Adapter for running the proxy pipeline through the orchestrator.

    Wraps the proxy pipeline, managing transport setup, background task
    lifecycle, and session persistence. Does not use the TUI.
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the proxy adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: transport, command, url, intercept.
        """
        self._runner = runner
        self._config = config
        self._child_id: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._session_store: SessionStore | None = None
        self._start_time: float | None = None

    async def start(self) -> str:
        """Start the proxy pipeline as a background task.

        Returns:
            The child run_id.
        """
        transport = _parse_transport(self._config["transport"])
        intercept = self._config.get("intercept", False)

        self._child_id = await self._runner.create_child_run("proxy")
        await self._runner.update_child_status(self._child_id, RunStatus.RUNNING)

        self._session_store = SessionStore(
            session_id=uuid.uuid4().hex,
            transport=transport,
            server_command=self._config.get("command"),
            server_url=self._config.get("url"),
        )

        intercept_engine = InterceptEngine(
            mode=InterceptMode.INTERCEPT if intercept else InterceptMode.PASSTHROUGH
        )

        pipeline_session = PipelineSession(
            session_store=self._session_store,
            intercept_engine=intercept_engine,
            transport=transport,
        )

        client_adapter, server_adapter = _create_adapters(self._config)

        self._start_time = time.monotonic()

        async def _run_pipeline_wrapper() -> None:
            async with client_adapter, server_adapter:
                await run_pipeline(client_adapter, server_adapter, pipeline_session)

        self._task = asyncio.create_task(_run_pipeline_wrapper())

        await self._runner.emit_progress(
            self._child_id, f"Proxy started on {self._config['transport']}"
        )
        return self._child_id

    async def stop(self) -> ProxyResult:
        """Stop the proxy pipeline and persist the session.

        Returns:
            ProxyResult with run_id, message_count, and duration_seconds.
        """
        assert self._child_id is not None, "start() must be called before stop()"
        assert self._task is not None, "start() must be called before stop()"
        assert self._session_store is not None, "start() must be called before stop()"

        child_status = RunStatus.COMPLETED
        duration = 0.0
        message_count = 0

        try:
            # Cancel the background task — CancelledError is the expected
            # shutdown signal; TimeoutError means the task didn't stop in time.
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.CancelledError:
                pass  # Expected: task was cancelled by us
            except TimeoutError:
                child_status = RunStatus.FAILED

            duration = time.monotonic() - (self._start_time or time.monotonic())
            message_count = len(self._session_store.get_messages())

            # Persist session via mapper — pass child run_id to skip run creation
            persist_session(
                self._session_store,
                db_path=self._runner._db_path,
                duration_seconds=duration,
                run_id=self._child_id,
            )
        except Exception:
            child_status = RunStatus.FAILED
            duration = time.monotonic() - (self._start_time or time.monotonic())
            message_count = len(self._session_store.get_messages())
            raise
        finally:
            await self._runner.update_child_status(self._child_id, child_status)
            await self._runner.emit_progress(
                self._child_id,
                f"Proxy stopped ({child_status.name.lower()}), {message_count} messages captured",
            )

        return ProxyResult(
            run_id=self._child_id,
            message_count=message_count,
            duration_seconds=duration,
        )
