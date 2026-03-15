"""Shared helpers for transport adapter tests.

Provides mock SDK stream factories and message builders used across
stdio, SSE, and Streamable HTTP adapter test suites.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse


def make_session_message(method: str = "tools/list", msg_id: int = 1) -> SessionMessage:
    """Build a SessionMessage wrapping a JSON-RPC request."""
    return SessionMessage(
        message=JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))
    )


def make_response_message(msg_id: int = 1) -> SessionMessage:
    """Build a SessionMessage wrapping a JSON-RPC response."""
    return SessionMessage(
        message=JSONRPCMessage(JSONRPCResponse(jsonrpc="2.0", id=msg_id, result={}))
    )


@asynccontextmanager
async def mock_sdk_streams(
    inbound: list[SessionMessage | Exception] | None = None,
) -> AsyncIterator[
    tuple[
        anyio.abc.ObjectReceiveStream[SessionMessage | Exception],
        anyio.abc.ObjectSendStream[SessionMessage],
    ]
]:
    """Create a mock that yields real anyio memory streams.

    Pre-loads ``inbound`` items into the read stream, then closes the
    send end so the reader sees end-of-stream after consuming them.

    Yields:
        (read_stream, write_stream) -- same shape as SDK transport context managers.
    """
    # read_stream: adapter reads FROM here (server/client -> adapter)
    read_send, read_recv = anyio.create_memory_object_stream[SessionMessage | Exception](
        max_buffer_size=16
    )
    # write_stream: adapter writes TO here (adapter -> server/client)
    write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](max_buffer_size=16)

    # Preload inbound items and close the send end
    if inbound:
        for item in inbound:
            read_send.send_nowait(item)
    read_send.close()

    # Expose write_recv so tests can inspect what the adapter sent
    yield read_recv, write_send  # type: ignore[misc]

    # Clean up
    write_send.close()
    read_recv.close()
    write_recv.close()
