"""Tests for the permissions scanner (permissions).

Tests the permissions scanner with synthetic ScanContext objects for edge cases.

Integration tests requiring fixture servers are skipped.
"""

import pytest

from q_ai.audit.scanner.permissions import (
    _EXCESSIVE_TOOL_THRESHOLD,
    PermissionsScanner,
    _check_param_constraints,
    _classify_tool_category,
)
from q_ai.mcp.models import ScanContext


@pytest.mark.skip(reason="requires fixture server")
class TestPermissionsScanner:
    """Test the permissions scanner against the fixture server."""

    @pytest.mark.asyncio
    async def test_finds_excessive_tools(self):
        pass

    @pytest.mark.asyncio
    async def test_finds_dangerous_capabilities(self):
        pass

    @pytest.mark.asyncio
    async def test_finds_unconstrained_params(self):
        pass

    @pytest.mark.asyncio
    async def test_finds_high_write_ratio(self):
        pass

    @pytest.mark.asyncio
    async def test_findings_have_remediation(self):
        pass

    @pytest.mark.asyncio
    async def test_all_findings_map_to_permissions(self):
        pass


class TestEdgeCases:
    """Test scanner behavior with synthetic contexts."""

    @pytest.mark.asyncio
    async def test_no_tools_produces_no_findings(self):
        """Empty tool list should produce no findings."""
        ctx = ScanContext(tools=[])
        scanner = PermissionsScanner()
        findings = await scanner.scan(ctx)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_few_safe_tools_no_findings(self):
        """Small number of safe tools should produce minimal findings."""
        ctx = ScanContext(
            tools=[
                {"name": "ping", "description": "Check health", "inputSchema": {}},
                {"name": "version", "description": "Get version", "inputSchema": {}},
            ]
        )
        scanner = PermissionsScanner()
        findings = await scanner.scan(ctx)

        # No excessive count, no dangerous tools, no write ratio (too few)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_threshold_boundary(self):
        """Exactly at threshold should NOT trigger excessive tools finding."""
        tools = [
            {"name": f"tool_{i}", "description": "Safe tool", "inputSchema": {}}
            for i in range(_EXCESSIVE_TOOL_THRESHOLD)
        ]
        ctx = ScanContext(tools=tools)
        scanner = PermissionsScanner()
        findings = await scanner.scan(ctx)

        excessive = [f for f in findings if f.rule_id == "QAI-PERM-001"]
        assert len(excessive) == 0, "At-threshold should not trigger"


class TestHelpers:
    """Test helper functions."""

    def test_classify_shell_tool(self):
        """Shell execution tools are classified correctly."""
        categories = _classify_tool_category(
            {"name": "run_command", "description": "Execute a shell command"}
        )
        labels = [c["label"] for c in categories]
        assert "Shell/Command Execution" in labels

    def test_classify_file_write_tool(self):
        """File write tools are classified correctly."""
        categories = _classify_tool_category(
            {"name": "write_file", "description": "Write content to a file"}
        )
        labels = [c["label"] for c in categories]
        assert "File System Write/Delete" in labels

    def test_classify_safe_tool(self):
        """Safe tools produce no category matches."""
        categories = _classify_tool_category(
            {"name": "ping", "description": "Check if server is alive"}
        )
        assert len(categories) == 0

    def test_classify_multiple_categories(self):
        """Tools matching multiple categories return all matches."""
        categories = _classify_tool_category(
            {"name": "execute_and_save", "description": "Execute query and write file"}
        )
        assert len(categories) >= 2

    def test_unconstrained_path_param(self):
        """Path parameters without constraints are flagged."""
        tool = {
            "name": "read_file",
            "description": "Read a file",
            "inputSchema": {
                "properties": {
                    "path": {"type": "string"},
                },
            },
        }
        issues = _check_param_constraints(tool)
        assert len(issues) == 1
        assert issues[0]["label"] == "file path"

    def test_constrained_param_not_flagged(self):
        """Parameters with enum constraints are not flagged."""
        tool = {
            "name": "read_file",
            "description": "Read a file",
            "inputSchema": {
                "properties": {
                    "path": {
                        "type": "string",
                        "enum": ["/etc/config.json", "/etc/settings.json"],
                    },
                },
            },
        }
        issues = _check_param_constraints(tool)
        assert len(issues) == 0

    def test_non_string_param_not_flagged(self):
        """Non-string parameters are not checked."""
        tool = {
            "name": "set_value",
            "description": "Set a value",
            "inputSchema": {
                "properties": {
                    "command_id": {"type": "integer"},
                },
            },
        }
        issues = _check_param_constraints(tool)
        assert len(issues) == 0

    def test_url_param_flagged(self):
        """URL parameters without constraints are flagged."""
        tool = {
            "name": "fetch",
            "description": "Fetch data",
            "inputSchema": {
                "properties": {
                    "url": {"type": "string"},
                },
            },
        }
        issues = _check_param_constraints(tool)
        assert len(issues) == 1
        assert issues[0]["label"] == "URL"
