"""Tests for the shadow servers scanner (shadow_servers).

Tests the scanner with synthetic data and verifies detection of
development indicators, known dev tools, debug tool exposure,
governance gaps, and ephemeral deployment markers.

Integration tests requiring fixture servers are skipped.
"""

import pytest

from q_ai.audit.scanner.shadow_servers import (
    ShadowServersScanner,
    _has_dev_description,
    _has_dev_indicator,
    _has_ephemeral_markers,
    _is_debug_tool,
    _match_known_dev_tool,
)
from q_ai.mcp.models import ScanContext, Severity


@pytest.mark.skip(reason="requires fixture server")
class TestShadowServersIntegration:
    """Integration tests against the fixture server."""

    @pytest.mark.asyncio
    async def test_detects_dev_indicator_in_name(self):
        pass

    @pytest.mark.asyncio
    async def test_detects_dev_indicator_in_version(self):
        pass

    @pytest.mark.asyncio
    async def test_detects_debug_tools(self):
        pass

    @pytest.mark.asyncio
    async def test_detects_multiple_debug_tools_summary(self):
        pass

    @pytest.mark.asyncio
    async def test_detects_governance_gap(self):
        pass

    @pytest.mark.asyncio
    async def test_detects_ephemeral_markers(self):
        pass

    @pytest.mark.asyncio
    async def test_all_findings_have_remediation(self):
        pass


class TestDevIndicators:
    """Synthetic tests for QAI-SHADOW-001: Development server indicators."""

    @pytest.mark.asyncio
    async def test_dev_in_name(self):
        """Server named 'my-dev-server' triggers QAI-SHADOW-001."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "my-dev-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert len(dev_findings) >= 1
        assert any(f.metadata.get("matched_pattern") == "dev" for f in dev_findings)

    @pytest.mark.asyncio
    async def test_staging_in_name(self):
        """Server named 'staging-api' triggers QAI-SHADOW-001."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "staging-api", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert len(dev_findings) >= 1

    @pytest.mark.asyncio
    async def test_production_name_no_finding(self):
        """Server named 'production-api' does not trigger QAI-SHADOW-001."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "production-api", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert len(dev_findings) == 0

    @pytest.mark.asyncio
    async def test_myapp_name_no_finding(self):
        """Server named 'MyApp' does not trigger QAI-SHADOW-001."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "MyApp", "version": "2.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert len(dev_findings) == 0

    @pytest.mark.asyncio
    async def test_case_insensitive_debug(self):
        """'DEBUG-Server' triggers QAI-SHADOW-001 (case-insensitive)."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "DEBUG-Server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert len(dev_findings) >= 1

    @pytest.mark.asyncio
    async def test_dev_in_version_only(self):
        """Dev indicator in version but not name triggers QAI-SHADOW-001."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "my-api", "version": "1.0.0-dev"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert len(dev_findings) >= 1
        assert any(f.metadata.get("field") == "version" for f in dev_findings)

    @pytest.mark.asyncio
    async def test_severity_is_low(self):
        """QAI-SHADOW-001 findings have LOW severity."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "dev-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-001"]
        assert all(f.severity == Severity.LOW for f in dev_findings)


class TestKnownDevTools:
    """Synthetic tests for QAI-SHADOW-002: Known development tool fingerprint."""

    @pytest.mark.asyncio
    async def test_mcp_inspector(self):
        """Server named 'MCP Inspector' triggers QAI-SHADOW-002."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "MCP Inspector", "version": "0.13.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_tool_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-002"]
        assert len(dev_tool_findings) >= 1
        assert dev_tool_findings[0].severity in (Severity.MEDIUM, Severity.HIGH)

    @pytest.mark.asyncio
    async def test_mcp_server_template(self):
        """Server named 'mcp-server-template' triggers QAI-SHADOW-002."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "mcp-server-template", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_tool_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-002"]
        assert len(dev_tool_findings) >= 1

    @pytest.mark.asyncio
    async def test_company_api_no_finding(self):
        """Server named 'my-company-api' does not trigger QAI-SHADOW-002."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "my-company-api", "version": "2.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_tool_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-002"]
        assert len(dev_tool_findings) == 0

    @pytest.mark.asyncio
    async def test_fastmcp_stable_no_finding(self):
        """FastMCP with stable version does not trigger QAI-SHADOW-002."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "FastMCP", "version": "2.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_tool_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-002"]
        assert len(dev_tool_findings) == 0

    @pytest.mark.asyncio
    async def test_empty_name_no_finding(self):
        """Empty server name does not trigger QAI-SHADOW-002."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "", "version": "0.13.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        dev_tool_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-002"]
        assert len(dev_tool_findings) == 0


class TestDebugTools:
    """Synthetic tests for QAI-SHADOW-003: Debug/test tool exposure."""

    @pytest.mark.asyncio
    async def test_debug_prefix(self):
        """Tool with debug_ prefix triggers QAI-SHADOW-003."""
        ctx = ScanContext(
            tools=[{"name": "debug_dump", "description": ""}],
            server_info={"name": "my-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        debug_findings = [
            f for f in findings if f.rule_id == "QAI-SHADOW-003" and f.tool_name == "debug_dump"
        ]
        assert len(debug_findings) >= 1

    @pytest.mark.asyncio
    async def test_test_prefix(self):
        """Tool with test_ prefix triggers QAI-SHADOW-003."""
        ctx = ScanContext(
            tools=[{"name": "test_connection", "description": ""}],
            server_info={"name": "my-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        debug_findings = [
            f
            for f in findings
            if f.rule_id == "QAI-SHADOW-003" and f.tool_name == "test_connection"
        ]
        assert len(debug_findings) >= 1

    @pytest.mark.asyncio
    async def test_three_debug_tools_summary(self):
        """3+ debug tools produce MEDIUM summary finding."""
        ctx = ScanContext(
            tools=[
                {"name": "debug_a", "description": ""},
                {"name": "test_b", "description": ""},
                {"name": "dump_c", "description": ""},
            ],
            server_info={"name": "my-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        summary = [f for f in findings if f.rule_id == "QAI-SHADOW-003" and "Multiple" in f.title]
        assert len(summary) == 1
        assert summary[0].severity == Severity.MEDIUM

    @pytest.mark.asyncio
    async def test_normal_tools_no_finding(self):
        """Normal tools do not trigger QAI-SHADOW-003."""
        ctx = ScanContext(
            tools=[
                {"name": "get_data", "description": "Retrieve data from API"},
                {"name": "process", "description": "Process request"},
            ],
            server_info={"name": "my-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        debug_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-003"]
        assert len(debug_findings) == 0


class TestGovernanceGap:
    """Synthetic tests for QAI-SHADOW-004: Governance metadata gap."""

    @pytest.mark.asyncio
    async def test_no_description_five_tools(self):
        """No description + 5 tools triggers QAI-SHADOW-004."""
        ctx = ScanContext(
            tools=[{"name": f"tool_{i}"} for i in range(5)],
            server_info={"name": "my-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        gov_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-004"]
        assert len(gov_findings) == 1

    @pytest.mark.asyncio
    async def test_no_description_three_tools_no_finding(self):
        """No description + 3 tools (below threshold) does not trigger."""
        ctx = ScanContext(
            tools=[{"name": f"tool_{i}"} for i in range(3)],
            server_info={"name": "my-server", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        gov_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-004"]
        assert len(gov_findings) == 0

    @pytest.mark.asyncio
    async def test_has_description_many_tools_no_finding(self):
        """Has description + 10 tools does not trigger."""
        ctx = ScanContext(
            tools=[{"name": f"tool_{i}"} for i in range(10)],
            server_info={
                "name": "my-server",
                "version": "1.0.0",
                "description": "Production API server",
            },
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        gov_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-004"]
        assert len(gov_findings) == 0


class TestEphemeralMarkers:
    """Synthetic tests for QAI-SHADOW-005: Ephemeral deployment markers."""

    @pytest.mark.asyncio
    async def test_uuid_server_name(self):
        """UUID server name triggers QAI-SHADOW-005."""
        ctx = ScanContext(
            tools=[],
            server_info={
                "name": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "version": "1.0.0",
            },
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        eph_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-005"]
        assert len(eph_findings) >= 1

    @pytest.mark.asyncio
    async def test_version_zero_zero_zero(self):
        """Version '0.0.0' triggers QAI-SHADOW-005."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "my-server", "version": "0.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        eph_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-005"]
        assert len(eph_findings) >= 1

    @pytest.mark.asyncio
    async def test_normal_name_no_finding(self):
        """Normal name 'my-api-server' does not trigger QAI-SHADOW-005."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "my-api-server", "version": "2.1.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        eph_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-005"]
        assert len(eph_findings) == 0

    @pytest.mark.asyncio
    async def test_severity_is_info(self):
        """QAI-SHADOW-005 findings have INFORMATIONAL severity."""
        ctx = ScanContext(
            tools=[],
            server_info={"name": "abc123def456", "version": "1.0.0"},
        )
        scanner = ShadowServersScanner()
        findings = await scanner.scan(ctx)

        eph_findings = [f for f in findings if f.rule_id == "QAI-SHADOW-005"]
        assert all(f.severity == Severity.INFO for f in eph_findings)


class TestHelpers:
    """Unit tests for helper functions."""

    def test_has_dev_indicator_dev(self):
        """'dev' matched in server name."""
        assert _has_dev_indicator("my-dev-server") == "dev"

    def test_has_dev_indicator_staging(self):
        """'staging' matched in server name."""
        assert _has_dev_indicator("staging-api") == "staging"

    def test_has_dev_indicator_none(self):
        """No match returns None."""
        assert _has_dev_indicator("production-api") is None

    def test_has_dev_indicator_case_insensitive(self):
        """Case-insensitive matching."""
        assert _has_dev_indicator("DEBUG-Server") == "debug"

    def test_has_dev_indicator_temp(self):
        """'temp' matched."""
        assert _has_dev_indicator("temp-worker") == "temp"

    def test_match_known_dev_tool_inspector(self):
        """MCP Inspector matched."""
        result = _match_known_dev_tool("MCP Inspector", "0.13.0")
        assert result is not None
        assert "inspector" in result["name_pattern"]

    def test_match_known_dev_tool_none(self):
        """Non-dev tool returns None."""
        assert _match_known_dev_tool("my-api", "1.0.0") is None

    def test_match_known_dev_tool_fastmcp_dev(self):
        """FastMCP with dev version matched."""
        result = _match_known_dev_tool("FastMCP Server", "0.5.0-dev")
        assert result is not None

    def test_match_known_dev_tool_fastmcp_stable(self):
        """FastMCP with stable version not matched."""
        assert _match_known_dev_tool("FastMCP", "2.0.0") is None

    def test_is_debug_tool_prefix(self):
        """debug_ prefix detected."""
        assert _is_debug_tool("debug_dump") is True

    def test_is_debug_tool_normal(self):
        """Normal tool not flagged."""
        assert _is_debug_tool("get_data") is False

    def test_is_debug_tool_test_prefix(self):
        """test_ prefix detected."""
        assert _is_debug_tool("test_echo") is True

    def test_has_dev_description_match(self):
        """Development phrase detected."""
        assert _has_dev_description("for development only") is not None

    def test_has_dev_description_none(self):
        """Normal description not flagged."""
        assert _has_dev_description("Process incoming requests") is None

    def test_has_dev_description_debug(self):
        """'debug purposes' detected."""
        assert _has_dev_description("Used for debug purposes") == "debug purposes"

    def test_has_ephemeral_markers_uuid(self):
        """UUID detected in name."""
        markers = _has_ephemeral_markers("a1b2c3d4-e5f6-7890-abcd-ef1234567890", "1.0.0")
        assert len(markers) >= 1
        assert any("UUID" in m for m in markers)

    def test_has_ephemeral_markers_docker_hex(self):
        """Docker hex hostname detected."""
        markers = _has_ephemeral_markers("abc123def456ab", "1.0.0")
        assert len(markers) >= 1

    def test_has_ephemeral_markers_snapshot(self):
        """SNAPSHOT version detected."""
        markers = _has_ephemeral_markers("server", "1.0.0-SNAPSHOT")
        assert len(markers) >= 1
        assert any("Snapshot" in m for m in markers)

    def test_has_ephemeral_markers_none(self):
        """Normal name and version produce no markers."""
        markers = _has_ephemeral_markers("my-api-server", "2.1.0")
        assert len(markers) == 0

    def test_has_ephemeral_markers_zero_version(self):
        """Version 0.0.0 detected as ephemeral."""
        markers = _has_ephemeral_markers("server", "0.0.0")
        assert len(markers) >= 1
