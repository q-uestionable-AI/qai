"""Integration tests for inject serve -- stdio server lifecycle.

Starts the inject server via stdio transport, connects an MCP client,
and verifies tools and responses work end-to-end.
"""

from __future__ import annotations

import sys

import pytest

from q_ai.inject.payloads.loader import load_all_templates
from q_ai.mcp.connection import MCPConnection
from q_ai.mcp.discovery import enumerate_server

pytestmark = pytest.mark.integration

PYTHON = sys.executable

_SERVER_SCRIPT = """\
import sys
sys.path.insert(0, "src")
from q_ai.inject.payloads.loader import load_all_templates
from q_ai.inject.server import build_server

templates = load_all_templates()
server = build_server(templates)
server.run(transport="stdio")
"""


class TestServeStdioIntegration:
    """Start a built inject server via stdio, connect, and interact."""

    @pytest.mark.asyncio
    async def test_connect_and_initialize(self) -> None:
        """Server responds to MCP initialize handshake."""
        async with MCPConnection.stdio(
            command=PYTHON,
            args=["-c", _SERVER_SCRIPT],
        ) as conn:
            assert conn.session is not None
            assert conn.init_result is not None

    @pytest.mark.asyncio
    async def test_tools_list_matches_templates(self) -> None:
        """tools/list returns tools matching loaded payload templates."""
        templates = load_all_templates()
        async with MCPConnection.stdio(
            command=PYTHON,
            args=["-c", _SERVER_SCRIPT],
        ) as conn:
            ctx = await enumerate_server(conn)
            tool_names = {t["name"] for t in ctx.tools}
            expected_names = {t.tool_name for t in templates}
            assert tool_names == expected_names

    @pytest.mark.asyncio
    async def test_tool_call_returns_response(self) -> None:
        """Calling a tool returns the templated response with substitution."""
        async with MCPConnection.stdio(
            command=PYTHON,
            args=["-c", _SERVER_SCRIPT],
        ) as conn:
            result = await conn.session.call_tool("get_weather", {"city": "London"})
            assert len(result.content) >= 1
            text = result.content[0].text
            assert "London" in text

    @pytest.mark.asyncio
    async def test_tool_descriptions_are_poisoned(self) -> None:
        """Tool descriptions contain the poisoned content from templates."""
        async with MCPConnection.stdio(
            command=PYTHON,
            args=["-c", _SERVER_SCRIPT],
        ) as conn:
            ctx = await enumerate_server(conn)
            weather_tools = [t for t in ctx.tools if t["name"] == "get_weather"]
            assert len(weather_tools) == 1
            desc = weather_tools[0].get("description", "")
            assert "<IMPORTANT>" in desc
