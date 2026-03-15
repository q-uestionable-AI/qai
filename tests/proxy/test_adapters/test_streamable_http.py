"""Tests for q_ai.proxy.adapters.streamable_http -- StreamableHttpServerAdapter.

Uses mocked SDK streamable_http_client() with real anyio memory object streams.
No network connections.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import anyio
from mcp.shared.message import SessionMessage

from q_ai.proxy.adapters.streamable_http import StreamableHttpServerAdapter

from .conftest import make_session_message, mock_sdk_streams

# ---------------------------------------------------------------------------
# StreamableHttpServerAdapter
# ---------------------------------------------------------------------------


class TestStreamableHttpServerAdapterRead:
    """Reading messages from the server via StreamableHttpServerAdapter."""

    async def test_read_returns_session_message(self) -> None:
        """Adapter.read() returns a SessionMessage from the SDK stream."""
        msg = make_session_message()

        @asynccontextmanager
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams(inbound=[msg]) as (r, w):
                yield r, w, lambda: None

        with patch(
            "q_ai.proxy.adapters.streamable_http.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
                result = await adapter.read()

        assert result.message == msg.message

    async def test_read_skips_exceptions(self, caplog: Any) -> None:
        """Exception items from the SDK stream are logged and skipped."""
        err = RuntimeError("parse error")
        msg = make_session_message()

        @asynccontextmanager
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams(inbound=[err, msg]) as (r, w):
                yield r, w, lambda: None

        with (
            patch(
                "q_ai.proxy.adapters.streamable_http.streamable_http_client",
                fake_streamable_http_client,
            ),
            caplog.at_level(logging.WARNING),
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
                result = await adapter.read()

        assert result.message == msg.message
        assert "parse error" in caplog.text


class TestStreamableHttpServerAdapterWrite:
    """Writing messages to the server via StreamableHttpServerAdapter."""

    async def test_write_sends_to_stream(self) -> None:
        """Adapter.write() sends the message through to the SDK stream."""
        msg = make_session_message()
        write_recv_ref: list[Any] = []

        @asynccontextmanager
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](
                max_buffer_size=16
            )
            write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](
                max_buffer_size=16
            )
            write_recv_ref.append(write_recv)
            yield read_recv, write_send, lambda: None
            read_send.close()
            read_recv.close()
            write_send.close()
            write_recv.close()

        with patch(
            "q_ai.proxy.adapters.streamable_http.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
                await adapter.write(msg)
                await asyncio.sleep(0.05)
                result = write_recv_ref[0].receive_nowait()
                assert result.message == msg.message


class TestStreamableHttpServerAdapterClose:
    """Lifecycle and close behavior for StreamableHttpServerAdapter."""

    async def test_close_is_idempotent(self) -> None:
        """Calling close() multiple times does not raise."""

        @asynccontextmanager
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w, lambda: None

        with patch(
            "q_ai.proxy.adapters.streamable_http.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
                await adapter.close()
                await adapter.close()  # should not raise

    async def test_read_on_closed_raises(self) -> None:
        """read() on a closed adapter raises RuntimeError."""

        @asynccontextmanager
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w, lambda: None

        with patch(
            "q_ai.proxy.adapters.streamable_http.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
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
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w, lambda: None

        with patch(
            "q_ai.proxy.adapters.streamable_http.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
                await adapter.close()
                try:
                    await adapter.write(msg)
                    raise AssertionError("Expected RuntimeError")
                except RuntimeError:
                    pass

    async def test_context_manager_lifecycle(self) -> None:
        """Entering and exiting the context manager works cleanly."""

        @asynccontextmanager
        async def fake_streamable_http_client(*args: Any, **kwargs: Any):
            async with mock_sdk_streams() as (r, w):
                yield r, w, lambda: None

        with patch(
            "q_ai.proxy.adapters.streamable_http.streamable_http_client",
            fake_streamable_http_client,
        ):
            async with StreamableHttpServerAdapter(url="http://fake:3000/mcp") as adapter:
                assert adapter is not None
            assert adapter._closed
