"""Tests for the programmable proxy runtime."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest
from mcp.shared.message import SessionMessage

from q_ai.mcp.models import Transport
from q_ai.mcp.transport import TransportClosedError
from q_ai.proxy.constants import LISTEN_HOST
from q_ai.proxy.intercept import InterceptEngine
from q_ai.proxy.pipeline import PipelineSession
from q_ai.proxy.runtime import (
    ProxyRuntime,
    ProxyRuntimeConfig,
    _build_client_adapter,
)
from q_ai.proxy.session_store import SessionStore


class QueueAdapter:
    """Controllable adapter and async context for runtime tests."""

    def __init__(self) -> None:
        self.read_queue: asyncio.Queue[SessionMessage | None] = asyncio.Queue()
        self.closed = False
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> QueueAdapter:
        self.entered = True
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.exited = True
        await self.close()

    async def read(self) -> SessionMessage:
        item = await self.read_queue.get()
        if item is None:
            raise TransportClosedError("test adapter closed")
        return item

    async def write(self, _message: SessionMessage) -> None:
        return

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.read_queue.put_nowait(None)


def _make_session() -> PipelineSession:
    store = SessionStore(session_id=str(uuid.uuid4()), transport=Transport.STDIO)
    return PipelineSession(
        session_store=store,
        intercept_engine=InterceptEngine(),
        transport=Transport.STDIO,
    )


class TestRuntimeConfig:
    """Runtime configuration rejects unsafe or incomplete combinations."""

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValueError, match="server_command is required"):
            ProxyRuntimeConfig(transport=Transport.STDIO)

    def test_network_target_requires_url(self) -> None:
        with pytest.raises(ValueError, match="server_url is required"):
            ProxyRuntimeConfig(transport=Transport.SSE)

    def test_listener_rejects_stdio(self) -> None:
        with pytest.raises(ValueError, match="listen_transport"):
            ProxyRuntimeConfig(
                transport=Transport.STDIO,
                server_command="python server.py",
                listen_transport=Transport.STDIO,
            )

    def test_network_listener_uses_loopback(self) -> None:
        config = ProxyRuntimeConfig(
            transport=Transport.STDIO,
            server_command="python server.py",
            listen_transport=Transport.SSE,
            listen_port=8123,
        )
        with patch("q_ai.proxy.runtime.SseClientAdapter") as adapter_factory:
            _build_client_adapter(config)
        adapter_factory.assert_called_once_with(host=LISTEN_HOST, port=8123)


class TestProxyRuntime:
    """ProxyRuntime owns readiness, cleanup, and session completion."""

    async def test_run_owns_adapter_contexts(self) -> None:
        client = QueueAdapter()
        server = QueueAdapter()
        session = _make_session()
        runtime = ProxyRuntime(session)
        config = ProxyRuntimeConfig(
            transport=Transport.STDIO,
            server_command="python server.py",
        )

        with (
            patch("q_ai.proxy.runtime._build_client_adapter", return_value=client),
            patch("q_ai.proxy.runtime._build_server_adapter", return_value=server),
        ):
            task = asyncio.create_task(runtime.run(config))
            await runtime.wait_until_ready()
            assert runtime.ready
            await runtime.stop()
            await task

        assert client.entered and client.exited
        assert server.entered and server.exited
        assert session.session_store.to_proxy_session().ended_at is not None
        assert not runtime.ready

    async def test_unexpected_failure_propagates_and_finishes_session(self) -> None:
        class FailingAdapter(QueueAdapter):
            async def read(self) -> SessionMessage:
                raise OSError("adapter read failed")

        session = _make_session()
        runtime = ProxyRuntime(session)

        with pytest.raises(ExceptionGroup) as exc_info:
            await runtime.run_with_adapters(FailingAdapter(), QueueAdapter())

        assert any("adapter read failed" in str(exc) for exc in exc_info.value.exceptions)
        assert session.session_store.to_proxy_session().ended_at is not None
        assert not runtime.ready
