"""Tests for q_ai.mcp.discovery — server capability enumeration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from q_ai.mcp.discovery import (
    _prompt_to_dict,
    _resource_to_dict,
    _tool_to_dict,
    enumerate_server,
)


class TestToolToDict:
    """_tool_to_dict conversion tests."""

    def test_basic_tool(self) -> None:
        tool = SimpleNamespace(
            name="read_file",
            title="Read File",
            description="Reads a file from disk",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
            outputSchema=None,
            annotations=None,
        )
        result = _tool_to_dict(tool)
        assert result["name"] == "read_file"
        assert result["title"] == "Read File"
        assert result["description"] == "Reads a file from disk"
        assert result["inputSchema"]["type"] == "object"
        assert result["outputSchema"] is None
        assert result["annotations"] is None

    def test_tool_with_no_description(self) -> None:
        tool = SimpleNamespace(
            name="my_tool",
            title=None,
            description=None,
            inputSchema=None,
            outputSchema=None,
            annotations=None,
        )
        result = _tool_to_dict(tool)
        assert result["description"] == ""
        assert result["inputSchema"] == {}

    def test_tool_with_annotations(self) -> None:
        annotations_mock = MagicMock()
        annotations_mock.model_dump.return_value = {"readOnly": True}
        tool = SimpleNamespace(
            name="safe_tool",
            title="Safe Tool",
            description="A safe tool",
            inputSchema={},
            outputSchema=None,
            annotations=annotations_mock,
        )
        result = _tool_to_dict(tool)
        assert result["annotations"] == {"readOnly": True}


class TestResourceToDict:
    """_resource_to_dict conversion tests."""

    def test_basic_resource(self) -> None:
        resource = SimpleNamespace(
            uri="file:///tmp/test.txt",
            name="test.txt",
            title="Test File",
            description="A test file",
            mimeType="text/plain",
        )
        result = _resource_to_dict(resource)
        assert result["uri"] == "file:///tmp/test.txt"
        assert result["name"] == "test.txt"
        assert result["title"] == "Test File"
        assert result["description"] == "A test file"
        assert result["mimeType"] == "text/plain"

    def test_resource_with_no_description(self) -> None:
        resource = SimpleNamespace(
            uri="file:///data",
            name="data",
            title=None,
            description=None,
            mimeType=None,
        )
        result = _resource_to_dict(resource)
        assert result["description"] == ""
        assert result["mimeType"] is None


class TestPromptToDict:
    """_prompt_to_dict conversion tests."""

    def test_basic_prompt(self) -> None:
        prompt = SimpleNamespace(
            name="greet",
            title="Greeting",
            description="Generates a greeting",
            arguments=[
                SimpleNamespace(name="name", description="Person's name", required=True),
            ],
        )
        result = _prompt_to_dict(prompt)
        assert result["name"] == "greet"
        assert result["title"] == "Greeting"
        assert result["description"] == "Generates a greeting"
        assert len(result["arguments"]) == 1
        assert result["arguments"][0]["name"] == "name"
        assert result["arguments"][0]["required"] is True

    def test_prompt_with_no_arguments(self) -> None:
        prompt = SimpleNamespace(
            name="help",
            title=None,
            description=None,
            arguments=None,
        )
        result = _prompt_to_dict(prompt)
        assert result["description"] == ""
        assert result["arguments"] == []

    def test_prompt_with_multiple_arguments(self) -> None:
        prompt = SimpleNamespace(
            name="generate",
            title="Generate",
            description="Generate content",
            arguments=[
                SimpleNamespace(name="topic", description="Topic", required=True),
                SimpleNamespace(name="style", description="", required=False),
            ],
        )
        result = _prompt_to_dict(prompt)
        assert len(result["arguments"]) == 2
        assert result["arguments"][1]["required"] is False


class TestEnumerateServer:
    """enumerate_server integration tests with mocked connection."""

    @pytest.fixture()
    def mock_conn(self) -> MagicMock:
        """Create a mock MCPConnection with capabilities."""
        conn = MagicMock()
        conn.transport_type = "stdio"
        conn.connection_url = None

        # Mock init_result
        conn.init_result.serverInfo.name = "test-server"
        conn.init_result.serverInfo.version = "1.0.0"
        conn.init_result.protocolVersion = "2025-03-26"
        conn.init_result.instructions = None
        conn.init_result.capabilities.tools = True
        conn.init_result.capabilities.resources = True
        conn.init_result.capabilities.prompts = True
        conn.init_result.capabilities.logging = None

        # Mock session methods
        session = AsyncMock()
        conn.session = session

        tool = SimpleNamespace(
            name="test_tool",
            title="Test Tool",
            description="A test tool",
            inputSchema={"type": "object"},
            outputSchema=None,
            annotations=None,
        )
        session.list_tools.return_value = SimpleNamespace(tools=[tool])

        resource = SimpleNamespace(
            uri="file:///test",
            name="test",
            title="Test",
            description="A test resource",
            mimeType="text/plain",
        )
        session.list_resources.return_value = SimpleNamespace(resources=[resource])

        prompt = SimpleNamespace(
            name="greet",
            title="Greet",
            description="A greeting",
            arguments=[],
        )
        session.list_prompts.return_value = SimpleNamespace(prompts=[prompt])

        return conn

    async def test_enumerates_tools(self, mock_conn: MagicMock) -> None:
        ctx = await enumerate_server(mock_conn)
        assert len(ctx.tools) == 1
        assert ctx.tools[0]["name"] == "test_tool"

    async def test_enumerates_resources(self, mock_conn: MagicMock) -> None:
        ctx = await enumerate_server(mock_conn)
        assert len(ctx.resources) == 1
        assert ctx.resources[0]["uri"] == "file:///test"

    async def test_enumerates_prompts(self, mock_conn: MagicMock) -> None:
        ctx = await enumerate_server(mock_conn)
        assert len(ctx.prompts) == 1
        assert ctx.prompts[0]["name"] == "greet"

    async def test_server_info_populated(self, mock_conn: MagicMock) -> None:
        ctx = await enumerate_server(mock_conn)
        assert ctx.server_info["name"] == "test-server"
        assert ctx.server_info["version"] == "1.0.0"
        assert ctx.server_info["transport"] == "stdio"

    async def test_skips_unsupported_capabilities(self, mock_conn: MagicMock) -> None:
        mock_conn.init_result.capabilities.tools = None
        mock_conn.init_result.capabilities.resources = None
        mock_conn.init_result.capabilities.prompts = None
        ctx = await enumerate_server(mock_conn)
        assert ctx.tools == []
        assert ctx.resources == []
        assert ctx.prompts == []

    async def test_transport_type_preserved(self, mock_conn: MagicMock) -> None:
        mock_conn.transport_type = "sse"
        mock_conn.connection_url = "http://localhost:8080/sse"
        ctx = await enumerate_server(mock_conn)
        assert ctx.transport_type == "sse"
        assert ctx.connection_url == "http://localhost:8080/sse"
