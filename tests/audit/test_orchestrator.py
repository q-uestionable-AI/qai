"""Tests for the audit orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from q_ai.audit.orchestrator import ScanResult, run_scan
from q_ai.audit.scanner.base import BaseScanner
from q_ai.core.frameworks import FrameworkResolver
from q_ai.core.mitigation import MitigationGuidance
from q_ai.mcp.models import ScanContext, ScanFinding, Severity


class FakeScanner(BaseScanner):
    """Fake scanner that returns pre-configured findings."""

    name = "fake"
    category = "command_injection"
    description = "Fake scanner for testing"

    def __init__(self, findings: list[ScanFinding] | None = None) -> None:
        self._findings = findings or []

    async def scan(self, context: ScanContext) -> list[ScanFinding]:
        """Return pre-configured findings."""
        return self._findings


class TestScanResult:
    def test_defaults(self) -> None:
        result = ScanResult()
        assert result.findings == []
        assert result.server_info == {}
        assert result.tools_scanned == 0
        assert result.scanners_run == []
        assert result.finished_at is None
        assert result.errors == []
        assert result.started_at is not None


class TestRunScan:
    @pytest.mark.asyncio
    async def test_run_scan_populates_framework_ids(self) -> None:
        """Verify that run_scan resolves framework_ids on every finding."""
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Test injection",
            description="Test description",
            severity=Severity.HIGH,
            evidence="test evidence",
        )
        scanner = FakeScanner(findings=[finding])

        mock_context = ScanContext(
            server_info={"name": "test-server", "version": "1.0"},
            tools=[{"name": "tool1"}],
            resources=[],
            prompts=[],
        )

        mock_conn = MagicMock()

        with (
            patch(
                "q_ai.audit.orchestrator.enumerate_server",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "q_ai.audit.orchestrator.get_all_scanners",
                return_value=[scanner],
            ),
        ):
            result = await run_scan(mock_conn)

        assert len(result.findings) == 1
        f = result.findings[0]
        # FrameworkResolver is NOT mocked -- we use the real one
        assert f.framework_ids != {}
        # command_injection should map to MCP05 in owasp_mcp_top10
        assert "owasp_mcp_top10" in f.framework_ids
        assert f.framework_ids["owasp_mcp_top10"] == "MCP05"
        # Mitigation should also be populated after framework mapping
        assert f.mitigation is not None
        assert isinstance(f.mitigation, MitigationGuidance)
        assert len(f.mitigation.sections) >= 2

    @pytest.mark.asyncio
    async def test_run_scan_with_specific_scanners(self) -> None:
        """Verify that check_names filters scanners."""
        finding = ScanFinding(
            rule_id="MCP05-001",
            category="command_injection",
            title="Test injection",
            description="Test description",
            severity=Severity.HIGH,
        )
        scanner = FakeScanner(findings=[finding])

        mock_context = ScanContext(
            server_info={"name": "test-server", "version": "1.0"},
            tools=[],
            resources=[],
            prompts=[],
        )

        mock_conn = MagicMock()

        with (
            patch(
                "q_ai.audit.orchestrator.enumerate_server",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "q_ai.audit.orchestrator.get_scanner",
                return_value=scanner,
            ),
        ):
            result = await run_scan(mock_conn, check_names=["fake"])

        assert len(result.findings) == 1
        assert "fake" in result.scanners_run

    @pytest.mark.asyncio
    async def test_run_scan_handles_scanner_error(self) -> None:
        """Verify that scanner errors are captured, not raised."""

        class FailingScanner(BaseScanner):
            name = "failing"
            category = "test"
            description = "Always fails"

            async def scan(self, context: ScanContext) -> list[ScanFinding]:
                raise RuntimeError("Scanner exploded")

        mock_context = ScanContext(
            server_info={"name": "test-server", "version": "1.0"},
            tools=[],
            resources=[],
            prompts=[],
        )

        mock_conn = MagicMock()

        with (
            patch(
                "q_ai.audit.orchestrator.enumerate_server",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "q_ai.audit.orchestrator.get_all_scanners",
                return_value=[FailingScanner()],
            ),
        ):
            result = await run_scan(mock_conn)

        assert len(result.errors) == 1
        assert result.errors[0]["scanner"] == "failing"
        assert "Scanner exploded" in result.errors[0]["error"]
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_run_scan_unknown_scanner_name(self) -> None:
        """Verify that unknown scanner names produce errors."""
        mock_context = ScanContext(
            server_info={"name": "test-server", "version": "1.0"},
            tools=[],
            resources=[],
            prompts=[],
        )

        mock_conn = MagicMock()

        with patch(
            "q_ai.audit.orchestrator.enumerate_server",
            new_callable=AsyncMock,
            return_value=mock_context,
        ):
            result = await run_scan(mock_conn, check_names=["nonexistent_scanner_xyz"])

        assert len(result.errors) == 1
        assert result.errors[0]["scanner"] == "nonexistent_scanner_xyz"

    @pytest.mark.asyncio
    async def test_run_scan_metadata_populated(self) -> None:
        """Verify server_info and tools_scanned are populated."""
        mock_context = ScanContext(
            server_info={"name": "my-server", "version": "2.0"},
            tools=[{"name": "t1"}, {"name": "t2"}],
            resources=[{"name": "r1"}],
            prompts=[],
        )

        mock_conn = MagicMock()

        with (
            patch(
                "q_ai.audit.orchestrator.enumerate_server",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "q_ai.audit.orchestrator.get_all_scanners",
                return_value=[],
            ),
        ):
            result = await run_scan(mock_conn)

        assert result.server_info == {"name": "my-server", "version": "2.0"}
        assert result.tools_scanned == 2
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_framework_ids_uses_real_resolver(self) -> None:
        """Verify multiple framework mappings are resolved correctly."""
        finding = ScanFinding(
            rule_id="MCP06-001",
            category="prompt_injection",
            title="Prompt injection",
            description="Prompt injection test",
            severity=Severity.HIGH,
        )
        scanner = FakeScanner(findings=[finding])

        mock_context = ScanContext(
            server_info={"name": "test-server", "version": "1.0"},
            tools=[],
            resources=[],
            prompts=[],
        )

        mock_conn = MagicMock()

        with (
            patch(
                "q_ai.audit.orchestrator.enumerate_server",
                new_callable=AsyncMock,
                return_value=mock_context,
            ),
            patch(
                "q_ai.audit.orchestrator.get_all_scanners",
                return_value=[scanner],
            ),
        ):
            result = await run_scan(mock_conn)

        f = result.findings[0]
        # prompt_injection maps to multiple frameworks
        resolver = FrameworkResolver()
        expected = resolver.resolve("prompt_injection")
        assert f.framework_ids == expected
        assert "owasp_mcp_top10" in f.framework_ids
