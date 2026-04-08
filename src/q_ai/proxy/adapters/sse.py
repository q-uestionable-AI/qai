"""SSE transport adapters for mcp-proxy.

Provides server-facing and client-facing adapters that bridge MCP SDK
anyio streams to asyncio queues. The pipeline never sees anyio — only
these adapters touch SDK transport internals.

SseServerAdapter wraps ``sse_client()`` — connects to a remote SSE MCP server.
SseClientAdapter wraps ``SseServerTransport`` — exposes an SSE endpoint
that remote MCP clients connect to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import TracebackType

import uvicorn
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.sse import sse_client
from mcp.server.sse import SseServerTransport
from mcp.shared.message import SessionMessage
from starlette.applications import Starlette
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)

# Sentinel pushed into the read queue when the SDK stream ends
_STREAM_CLOSED = object()


class SseServerAdapter:
    """Server-facing adapter — proxy connects to a remote MCP server via SSE.

    Wraps the MCP SDK ``sse_client()`` context manager. Connects to the
    target SSE server and bridges its anyio streams to asyncio queues
    for consumption by the pipeline.

    Args:
        url: The SSE endpoint URL of the remote MCP server.
        headers: Optional HTTP headers to send with the connection.
        timeout: Connection timeout in seconds.
        sse_read_timeout: SSE read timeout in seconds.

    Example:
        async with SseServerAdapter(url="http://localhost:3000/sse") as adapter:
            msg = await adapter.read()
            await adapter.write(response)
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
        sse_read_timeout: float = 300.0,
    ) -> None:
        self._url = url
        self._headers = headers
        self._timeout = timeout
        self._sse_read_timeout = sse_read_timeout
        self._read_queue: asyncio.Queue[SessionMessage | object] = asyncio.Queue()
        self._write_queue: asyncio.Queue[SessionMessage] = asyncio.Queue()
        self._closed = False
        self._reader_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SseServerAdapter:
        """Enter the adapter context — start SDK transport and bridge tasks."""
        self._sdk_cm = sse_client(
            self._url,
            headers=self._headers,
            timeout=self._timeout,
            sse_read_timeout=self._sse_read_timeout,
        )
        read_stream, write_stream = await self._sdk_cm.__aenter__()
        self._reader_task = asyncio.create_task(
            self._reader_loop(read_stream),
            name="sse-server-reader",
        )
        self._writer_task = asyncio.create_task(
            self._writer_loop(write_stream),
            name="sse-server-writer",
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
            raise RuntimeError("SseServerAdapter is closed")
        item = await self._read_queue.get()
        if item is _STREAM_CLOSED:
            raise RuntimeError("SseServerAdapter is closed")
        return item  # type: ignore[return-value]

    async def write(self, message: SessionMessage) -> None:
        """Write a message to the server.

        Args:
            message: The SessionMessage to send to the server.

        Raises:
            RuntimeError: If the adapter has been closed.
        """
        if self._closed:
            raise RuntimeError("SseServerAdapter is closed")
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


class SseClientAdapter:
    """Client-facing adapter — remote MCP client connects to proxy via SSE.

    Exposes an SSE endpoint backed by ``SseServerTransport``. Starts an
    embedded Starlette + uvicorn server on the given host/port. When an
    MCP client connects, the SDK's anyio streams are bridged to asyncio
    queues for consumption by the pipeline.

    Args:
        host: Bind address for the HTTP server.
        port: Port number for the HTTP server.

    Example:
        async with SseClientAdapter(host="0.0.0.0", port=8090) as adapter:
            msg = await adapter.read()   # message from remote client
            await adapter.write(response) # response to remote client
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:  # noqa: S104
        self._host = host
        self._port = port
        self._read_queue: asyncio.Queue[SessionMessage | object] = asyncio.Queue()
        self._write_queue: asyncio.Queue[SessionMessage] = asyncio.Queue()
        self._closed = False
        self._reader_task: asyncio.Task[None] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._uvicorn_server: uvicorn.Server | None = None
        self._client_connected: asyncio.Event = asyncio.Event()

    async def __aenter__(self) -> SseClientAdapter:
        """Enter the adapter context — start HTTP server and wait for client."""
        self._sse_transport = SseServerTransport("/messages")

        async def handle_sse(scope, receive, send):  # type: ignore[no-untyped-def]
            """ASGI handler for SSE connection from MCP client."""
            async with self._sse_transport.connect_sse(scope, receive, send) as streams:
                read_stream, write_stream = streams
                self._reader_task = asyncio.create_task(
                    self._reader_loop(read_stream),
                    name="sse-client-reader",
                )
                self._writer_task = asyncio.create_task(
                    self._writer_loop(write_stream),
                    name="sse-client-writer",
                )
                self._client_connected.set()
                # Keep connection alive until adapter closes
                try:
                    while not self._closed:
                        await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    pass

        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages", app=self._sse_transport.handle_post_message),
            ],
        )

        config = uvicorn.Config(
            app=starlette_app,
            host=self._host,
            port=self._port,
            log_level="warning",
            loop="none",
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(
            self._uvicorn_server.serve(),
            name="sse-client-uvicorn",
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the adapter context — shut down server and clean up."""
        await self.close()

    async def read(self) -> SessionMessage:
        """Read the next message from the remote client.

        Returns:
            The next SessionMessage from the client.

        Raises:
            RuntimeError: If the adapter has been closed.
        """
        if self._closed:
            raise RuntimeError("SseClientAdapter is closed")
        item = await self._read_queue.get()
        if item is _STREAM_CLOSED:
            raise RuntimeError("SseClientAdapter is closed")
        return item  # type: ignore[return-value]

    async def write(self, message: SessionMessage) -> None:
        """Write a message to the remote client.

        Args:
            message: The SessionMessage to send to the client.

        Raises:
            RuntimeError: If the adapter has been closed.
        """
        if self._closed:
            raise RuntimeError("SseClientAdapter is closed")
        await self._write_queue.put(message)

    async def close(self) -> None:
        """Shut down the adapter. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        await self._read_queue.put(_STREAM_CLOSED)
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer_task
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._server_task

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
                    logger.warning("Exception from client stream: %s", item)
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
