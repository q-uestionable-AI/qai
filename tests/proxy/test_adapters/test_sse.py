"""Tests for q_ai.proxy.adapters.sse -- SseServerAdapter and SseClientAdapter.

Uses mocked SDK sse_client() / SseServerTransport with real anyio memory
object streams. No network connections.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import anyio
from mcp.shared.message import SessionMessage

from q_ai.proxy.adapters.sse import SseClientAdapter, SseServerAdapter

from .conftest import make_session_message, mock_sdk_streams

# ---------------------------------------------------------------------------
# SseServerAdapter
# ---------------------------------------------------------------------------


class TestSseServerAdapterRead:
    """Reading messages from the server via SseServerAdapter."""

    async def test_read_returns_session_message(self) -> None:
        """Adapter.read() returns a SessionMessage from the SDK stream."""
        msg = make_session_message()

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams(inbound=[msg]) as (r, w):
                yield r, w

        with patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                result = await adapter.read()

        assert result.message == msg.message

    async def test_read_skips_exceptions(self, caplog: Any) -> None:
        """Exception items from the SDK stream are logged and skipped."""
        err = RuntimeError("parse error")
        msg = make_session_message()

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams(inbound=[err, msg]) as (r, w):
                yield r, w

        with (
            patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client),
            caplog.at_level(logging.WARNING),
        ):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                result = await adapter.read()

        assert result.message == msg.message
        assert "parse error" in caplog.text


class TestSseServerAdapterWrite:
    """Writing messages to the server via SseServerAdapter."""

    async def test_write_sends_to_stream(self) -> None:
        """Adapter.write() sends the message through to the SDK stream."""
        msg = make_session_message()
        write_recv_ref: list[Any] = []

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](
                max_buffer_size=16
            )
            write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](
                max_buffer_size=16
            )
            write_recv_ref.append(write_recv)
            yield read_recv, write_send
            read_send.close()
            read_recv.close()
            write_send.close()
            write_recv.close()

        with patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                await adapter.write(msg)
                await asyncio.sleep(0.05)
                result = write_recv_ref[0].receive_nowait()
                assert result.message == msg.message


class TestSseServerAdapterClose:
    """Lifecycle and close behavior for SseServerAdapter."""

    async def test_close_is_idempotent(self) -> None:
        """Calling close() multiple times does not raise."""

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w

        with patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                await adapter.close()
                await adapter.close()  # should not raise

    async def test_read_on_closed_raises(self) -> None:
        """read() on a closed adapter raises RuntimeError."""

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w

        with patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                await adapter.close()
                try:
                    await asyncio.wait_for(adapter.read(), timeout=0.5)
                    raise AssertionError("Expected RuntimeError")
                except RuntimeError:
                    pass

    async def test_write_on_closed_raises(self) -> None:
        """write() on a closed adapter raises RuntimeError."""
        msg = make_session_message()

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w

        with patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                await adapter.close()
                try:
                    await adapter.write(msg)
                    raise AssertionError("Expected RuntimeError")
                except RuntimeError:
                    pass

    async def test_context_manager_lifecycle(self) -> None:
        """Entering and exiting the context manager works cleanly."""

        @asynccontextmanager
        async def fake_sse_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w

        with patch("q_ai.proxy.adapters.sse.sse_client", fake_sse_client):
            async with SseServerAdapter(url="http://fake:3000/sse") as adapter:
                assert adapter is not None
            assert adapter._closed


# ---------------------------------------------------------------------------
# SseClientAdapter
# ---------------------------------------------------------------------------


def _make_sse_client_adapter_with_streams(
    inbound: list[SessionMessage | Exception] | None = None,
) -> tuple[SseClientAdapter, list[Any]]:
    """Build an SseClientAdapter with a patched SseServerTransport.

    Instead of starting uvicorn and waiting for real HTTP connections,
    we patch the transport so that connect_sse yields mock streams,
    and we skip the uvicorn server entirely. The reader/writer loops
    are started directly — same as if a client had connected.

    Returns:
        (adapter, write_recv_ref) where write_recv_ref collects the
        receive-end of the write stream for test inspection.
    """
    adapter = SseClientAdapter(host="127.0.0.1", port=0)
    adapter._write_recv_ref: list[Any] = []  # type: ignore[attr-defined]
    return adapter, adapter._write_recv_ref  # type: ignore[attr-defined]


async def _enter_sse_client_adapter(
    inbound: list[SessionMessage | Exception] | None = None,
) -> tuple[SseClientAdapter, list[Any]]:
    """Create and enter an SseClientAdapter with mock streams.

    Bypasses uvicorn — directly wires reader/writer loops to mock streams.

    Returns:
        (adapter, write_recv_ref) for test inspection.
    """
    adapter = SseClientAdapter(host="127.0.0.1", port=0)
    write_recv_ref: list[Any] = []

    # Create mock streams
    read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](
        max_buffer_size=16
    )
    write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](max_buffer_size=16)
    write_recv_ref.append(write_recv)

    if inbound:
        for item in inbound:
            read_send.send_nowait(item)
    read_send.close()

    # Wire up the reader/writer loops directly (bypass uvicorn + ASGI)
    adapter._reader_task = asyncio.create_task(
        adapter._reader_loop(read_recv),
        name="sse-client-reader-test",
    )
    adapter._writer_task = asyncio.create_task(
        adapter._writer_loop(write_send),
        name="sse-client-writer-test",
    )
    return adapter, write_recv_ref


class TestSseClientAdapterRead:
    """Reading messages from the client via SseClientAdapter."""

    async def test_read_returns_session_message(self) -> None:
        """Adapter.read() returns a SessionMessage from the SDK stream."""
        msg = make_session_message()
        adapter, _ = await _enter_sse_client_adapter(inbound=[msg])
        try:
            result = await adapter.read()
            assert result.message == msg.message
        finally:
            await adapter.close()

    async def test_read_skips_exceptions(self, caplog: Any) -> None:
        """Exception items from the SDK stream are logged and skipped."""
        err = RuntimeError("parse error")
        msg = make_session_message()
        adapter, _ = await _enter_sse_client_adapter(inbound=[err, msg])
        try:
            with caplog.at_level(logging.WARNING):
                result = await adapter.read()
            assert result.message == msg.message
            assert "parse error" in caplog.text
        finally:
            await adapter.close()


class TestSseClientAdapterWrite:
    """Writing messages to the client via SseClientAdapter."""

    async def test_write_sends_to_stream(self) -> None:
        """Adapter.write() sends the message through to the SDK stream."""
        msg = make_session_message()
        adapter, write_recv_ref = await _enter_sse_client_adapter()
        try:
            await adapter.write(msg)
            await asyncio.sleep(0.05)
            result = write_recv_ref[0].receive_nowait()
            assert result.message == msg.message
        finally:
            await adapter.close()


class TestSseClientAdapterClose:
    """Lifecycle and close behavior for SseClientAdapter."""

    async def test_close_is_idempotent(self) -> None:
        """Calling close() multiple times does not raise."""
        adapter, _ = await _enter_sse_client_adapter()
        await adapter.close()
        await adapter.close()  # should not raise

    async def test_read_on_closed_raises(self) -> None:
        """read() on a closed adapter raises RuntimeError."""
        adapter, _ = await _enter_sse_client_adapter()
        await adapter.close()
        try:
            await asyncio.wait_for(adapter.read(), timeout=0.5)
            raise AssertionError("Expected RuntimeError")
        except RuntimeError:
            pass

    async def test_write_on_closed_raises(self) -> None:
        """write() on a closed adapter raises RuntimeError."""
        msg = make_session_message()
        adapter, _ = await _enter_sse_client_adapter()
        await adapter.close()
        try:
            await adapter.write(msg)
            raise AssertionError("Expected RuntimeError")
        except RuntimeError:
            pass

    async def test_context_manager_cleanup(self) -> None:
        """Exiting the adapter sets _closed to True."""
        adapter, _ = await _enter_sse_client_adapter()
        assert not adapter._closed
        await adapter.close()
        assert adapter._closed
