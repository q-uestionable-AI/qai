"""Streamable HTTP transport adapter for mcp-proxy.

Provides a server-facing adapter that bridges the MCP SDK Streamable HTTP
client anyio streams to asyncio queues. The pipeline never sees anyio — only
this adapter touches SDK transport internals.

StreamableHttpServerAdapter wraps ``streamable_http_client()`` — connects to
a remote Streamable HTTP MCP server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from types import TracebackType

from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

# Sentinel pushed into the read queue when the SDK stream ends
_STREAM_CLOSED = object()


class StreamableHttpServerAdapter:
    """Server-facing adapter — proxy connects to a remote MCP server via Streamable HTTP.

    Wraps the MCP SDK ``streamable_http_client()`` context manager. Connects
    to the target Streamable HTTP server and bridges its anyio streams to
    asyncio queues for consumption by the pipeline.

    Args:
        url: The Streamable HTTP endpoint URL of the remote MCP server.
        terminate_on_close: Whether to send a terminate request when closing.

    Example:
        async with StreamableHttpServerAdapter(url="http://localhost:3000/mcp") as adapter:
            msg = await adapter.read()
            await adapter.write(response)
    """

    def __init__(
        self,
        url: str,
        terminate_on_close: bool = True,
    ) -> None:
        self._url = url
        self._terminate_on_close = terminate_on_close
        self._read_queue: asyncio.Queue[SessionMessage | object] = asyncio.Queue()
        self._write_queue: asyncio.Queue[SessionMessage] = asyncio.Queue()
        self._closed = False
        self._reader_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._get_session_id: Callable[[], str | None] | None = None

    async def __aenter__(self) -> StreamableHttpServerAdapter:
        """Enter the adapter context — start SDK transport and bridge tasks."""
        self._sdk_cm = streamable_http_client(
            self._url,
            terminate_on_close=self._terminate_on_close,
        )
        read_stream, write_stream, get_session_id = await self._sdk_cm.__aenter__()
        self._get_session_id = get_session_id
        self._reader_task = asyncio.create_task(
            self._reader_loop(read_stream),
            name="streamable-http-server-reader",
        )
        self._writer_task = asyncio.create_task(
            self._writer_loop(write_stream),
            name="streamable-http-server-writer",
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the adapter context — clean up tasks and SDK transport."""
        await self.close()
        if hasattr(self, "_sdk_cm"):
            try:
                await self._sdk_cm.__aexit__(exc_type, exc_val, exc_tb)
            except Exception:
                logger.debug("SDK context exit error (suppressed)", exc_info=True)

    async def read(self) -> SessionMessage:
        """Read the next message from the server.

        Returns:
            The next SessionMessage from the server.

        Raises:
            RuntimeError: If the adapter has been closed.
        """
        if self._closed:
            raise RuntimeError("StreamableHttpServerAdapter is closed")
        item = await self._read_queue.get()
        if item is _STREAM_CLOSED:
            raise RuntimeError("StreamableHttpServerAdapter is closed")
        return item  # type: ignore[return-value]

    async def write(self, message: SessionMessage) -> None:
        """Write a message to the server.

        Args:
            message: The SessionMessage to send to the server.

        Raises:
            RuntimeError: If the adapter has been closed.
        """
        if self._closed:
            raise RuntimeError("StreamableHttpServerAdapter is closed")
        await self._write_queue.put(message)

    async def close(self) -> None:
        """Shut down the adapter. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        # Signal the read queue so any waiting read() unblocks
        await self._read_queue.put(_STREAM_CLOSED)
        # Cancel bridge tasks
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer_task

    async def _reader_loop(
        self,
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
    ) -> None:
        """Bridge: pull from SDK anyio read stream, push to asyncio queue.

        Args:
            read_stream: The anyio receive stream from the SDK.
        """
        try:
            async for item in read_stream:
                if self._closed:
                    break
                if isinstance(item, Exception):
                    logger.warning("Exception from server stream: %s", item)
                    continue
                await self._read_queue.put(item)
        except Exception:
            if not self._closed:
                logger.debug("Reader loop ended", exc_info=True)
        finally:
            if not self._closed:
                await self._read_queue.put(_STREAM_CLOSED)

    async def _writer_loop(
        self,
        write_stream: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        """Bridge: pull from asyncio queue, send to SDK anyio write stream.

        Args:
            write_stream: The anyio send stream from the SDK.
        """
        try:
            while not self._closed:
                message = await self._write_queue.get()
                if self._closed:
                    break
                await write_stream.send(message)
        except Exception:
            if not self._closed:
                logger.debug("Writer loop ended", exc_info=True)
