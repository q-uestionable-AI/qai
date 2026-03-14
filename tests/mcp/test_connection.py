"""Tests for q_ai.mcp.connection — MCP server connector."""

from __future__ import annotations

import pytest

from q_ai.mcp.connection import MCPConnection


class TestStdioFactory:
    """MCPConnection.stdio factory method tests."""

    def test_creates_stdio_connection(self) -> None:
        conn = MCPConnection.stdio("python", ["-m", "my_server"])
        assert conn.transport_type == "stdio"

    def test_stores_command_and_args(self) -> None:
        conn = MCPConnection.stdio("node", ["server.js"])
        assert conn._transport_args["command"] == "node"
        assert conn._transport_args["args"] == ["server.js"]

    def test_default_args_is_empty_list(self) -> None:
        conn = MCPConnection.stdio("python")
        assert conn._transport_args["args"] == []

    def test_optional_env_and_cwd(self) -> None:
        conn = MCPConnection.stdio("python", ["-m", "server"], env={"KEY": "val"}, cwd="/tmp")
        assert conn._transport_args["env"] == {"KEY": "val"}
        assert conn._transport_args["cwd"] == "/tmp"

    def test_connection_url_is_none(self) -> None:
        conn = MCPConnection.stdio("python")
        assert conn.connection_url is None


class TestSseFactory:
    """MCPConnection.sse factory method tests."""

    def test_creates_sse_connection(self) -> None:
        conn = MCPConnection.sse("http://localhost:8080/sse")
        assert conn.transport_type == "sse"

    def test_stores_url(self) -> None:
        conn = MCPConnection.sse("http://localhost:8080/sse")
        assert conn._transport_args["url"] == "http://localhost:8080/sse"

    def test_connection_url_returns_url(self) -> None:
        conn = MCPConnection.sse("http://localhost:8080/sse")
        assert conn.connection_url == "http://localhost:8080/sse"

    def test_optional_headers(self) -> None:
        conn = MCPConnection.sse(
            "http://localhost:8080/sse", headers={"Authorization": "Bearer tok"}
        )
        assert conn._transport_args["headers"]["Authorization"] == "Bearer tok"


class TestStreamableHttpFactory:
    """MCPConnection.streamable_http factory method tests."""

    def test_creates_streamable_http_connection(self) -> None:
        conn = MCPConnection.streamable_http("http://localhost:8080/mcp")
        assert conn.transport_type == "streamable-http"

    def test_stores_url(self) -> None:
        conn = MCPConnection.streamable_http("http://localhost:8080/mcp")
        assert conn._transport_args["url"] == "http://localhost:8080/mcp"

    def test_connection_url_returns_url(self) -> None:
        conn = MCPConnection.streamable_http("http://localhost:8080/mcp")
        assert conn.connection_url == "http://localhost:8080/mcp"

    def test_optional_headers(self) -> None:
        conn = MCPConnection.streamable_http(
            "http://localhost:8080/mcp", headers={"X-Custom": "value"}
        )
        assert conn._transport_args["headers"]["X-Custom"] == "value"


class TestMCPConnectionErrors:
    def test_unknown_transport_raises(self) -> None:
        conn = MCPConnection(transport_type="unknown")
        with pytest.raises(ValueError, match="Unknown transport type"):
            import asyncio

            asyncio.run(conn._open_transport())

    async def test_aenter_cleanup_on_failure(self) -> None:
        """Verify resources are cleaned up if initialization fails."""
        conn = MCPConnection.stdio("nonexistent_command_that_does_not_exist_xyz")
        with pytest.raises(ConnectionError):
            async with conn:
                pass  # Should not reach here
