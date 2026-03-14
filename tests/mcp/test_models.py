"""Tests for q_ai.mcp.models — MCP domain data models."""

from __future__ import annotations

from datetime import UTC, datetime

from q_ai.mcp.models import Direction, ScanContext, ScanFinding, Severity, Transport


class TestSeverity:
    """Severity enum tests."""

    def test_all_values(self) -> None:
        assert Severity.CRITICAL == "critical"
        assert Severity.HIGH == "high"
        assert Severity.MEDIUM == "medium"
        assert Severity.LOW == "low"
        assert Severity.INFO == "info"

    def test_member_count(self) -> None:
        assert len(Severity) == 5

    def test_is_string(self) -> None:
        assert isinstance(Severity.HIGH, str)


class TestTransport:
    """Transport enum tests."""

    def test_all_values(self) -> None:
        assert Transport.STDIO == "stdio"
        assert Transport.SSE == "sse"
        assert Transport.STREAMABLE_HTTP == "streamable-http"

    def test_member_count(self) -> None:
        assert len(Transport) == 3


class TestDirection:
    """Direction enum tests."""

    def test_all_values(self) -> None:
        assert Direction.CLIENT_TO_SERVER == "client_to_server"
        assert Direction.SERVER_TO_CLIENT == "server_to_client"

    def test_member_count(self) -> None:
        assert len(Direction) == 2


class TestScanFinding:
    """ScanFinding dataclass tests."""

    def test_construction_with_required_fields(self) -> None:
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="MCP05",
            title="Test finding",
            description="A test finding",
            severity=Severity.HIGH,
        )
        assert finding.rule_id == "MCP05-001"
        assert finding.category == "MCP05"
        assert finding.title == "Test finding"
        assert finding.description == "A test finding"
        assert finding.severity == Severity.HIGH

    def test_defaults(self) -> None:
        finding = ScanFinding(
            rule_id="TEST-001",
            category="TEST",
            title="Test",
            description="Test",
            severity=Severity.INFO,
        )
        assert finding.evidence == ""
        assert finding.remediation == ""
        assert finding.tool_name == ""
        assert finding.metadata == {}
        assert finding.framework_ids == {}
        assert isinstance(finding.timestamp, datetime)
        assert finding.timestamp.tzinfo is not None

    def test_construction_with_all_fields(self) -> None:
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="MCP05",
            title="Injection",
            description="Command injection found",
            severity=Severity.CRITICAL,
            evidence="shell output observed",
            remediation="sanitize input",
            tool_name="run_query",
            metadata={"payload": "test"},
            timestamp=ts,
            framework_ids={"owasp_mcp": "MCP05"},
        )
        assert finding.tool_name == "run_query"
        assert finding.metadata == {"payload": "test"}
        assert finding.timestamp == ts
        assert finding.framework_ids == {"owasp_mcp": "MCP05"}

    def test_metadata_isolation(self) -> None:
        """Each instance gets its own metadata dict."""
        f1 = ScanFinding(
            rule_id="A", category="A", title="A", description="A", severity=Severity.LOW
        )
        f2 = ScanFinding(
            rule_id="B", category="B", title="B", description="B", severity=Severity.LOW
        )
        f1.metadata["key"] = "value"
        assert "key" not in f2.metadata


class TestScanContext:
    """ScanContext dataclass tests."""

    def test_defaults(self) -> None:
        ctx = ScanContext()
        assert ctx.server_info == {}
        assert ctx.tools == []
        assert ctx.resources == []
        assert ctx.prompts == []
        assert ctx.transport_type == "stdio"
        assert ctx.connection_url is None
        assert ctx.session is None
        assert ctx.config == {}

    def test_construction_with_data(self) -> None:
        ctx = ScanContext(
            server_info={"name": "test-server"},
            tools=[{"name": "my_tool"}],
            resources=[{"uri": "file:///tmp/test"}],
            prompts=[{"name": "greet"}],
            transport_type="sse",
            connection_url="http://localhost:8080/sse",
            session="fake-session",
            config={"timeout": 10},
        )
        assert ctx.server_info["name"] == "test-server"
        assert len(ctx.tools) == 1
        assert len(ctx.resources) == 1
        assert len(ctx.prompts) == 1
        assert ctx.transport_type == "sse"
        assert ctx.connection_url == "http://localhost:8080/sse"
        assert ctx.session == "fake-session"
        assert ctx.config["timeout"] == 10
