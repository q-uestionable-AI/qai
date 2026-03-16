"""Adapter for running audit scans through the orchestrator.

Wraps the audit orchestrator's run_scan() function, handling connection
setup, child run lifecycle, DB persistence, and event emission.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from q_ai.audit.mapper import _map_severity, persist_scan
from q_ai.audit.orchestrator import ScanResult, run_scan
from q_ai.core.models import RunStatus
from q_ai.mcp.connection import MCPConnection

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner


@dataclass
class AuditResult:
    """Result from an audit adapter run."""

    run_id: str
    scan_result: ScanResult
    finding_count: int


def _build_connection(config: dict[str, Any]) -> MCPConnection:
    """Create an MCPConnection from adapter config.

    Args:
        config: Adapter configuration dict with transport, command, url keys.

    Returns:
        Configured MCPConnection (not yet connected).

    Raises:
        ValueError: If required args are missing for the transport.
    """
    transport = config["transport"]
    if transport == "stdio":
        command = config.get("command")
        if not command:
            raise ValueError("'command' is required for stdio transport")
        parts = shlex.split(command)
        return MCPConnection.stdio(command=parts[0], args=parts[1:])
    if transport == "sse":
        url = config.get("url")
        if not url:
            raise ValueError("'url' is required for SSE transport")
        return MCPConnection.sse(url=url)
    if transport == "streamable-http":
        url = config.get("url")
        if not url:
            raise ValueError("'url' is required for streamable-http transport")
        return MCPConnection.streamable_http(url=url)
    raise ValueError(f"Unknown transport: {transport}")


class AuditAdapter:
    """Adapter for running audit scans through the orchestrator.

    Wraps the audit orchestrator's run_scan() function, handling connection
    setup, child run lifecycle, DB persistence, and event emission.
    """

    def __init__(
        self,
        runner: WorkflowRunner,
        config: dict[str, Any],
    ) -> None:
        """Initialize the audit adapter.

        Args:
            runner: WorkflowRunner managing the parent workflow.
            config: Configuration dict with keys: transport, command, url, checks.
        """
        self._runner = runner
        self._config = config

    async def run(self) -> AuditResult:
        """Execute an audit scan within the orchestrator lifecycle.

        Creates a child run, connects to the MCP server, runs scanners,
        persists results, and emits events.

        Returns:
            AuditResult with run_id, scan_result, and finding_count.
        """
        child_id = await self._runner.create_child_run("audit")
        await self._runner.update_child_status(child_id, RunStatus.RUNNING)

        try:
            await self._runner.emit_progress(child_id, "Connecting...")

            conn = _build_connection(self._config)
            async with conn:
                check_names = self._config.get("checks")
                server_name = (
                    conn.init_result.serverInfo.name if conn.init_result.serverInfo else "server"
                )
                await self._runner.emit_progress(child_id, f"Scanning {server_name}...")

                scan_result = await run_scan(conn, check_names=check_names)

            finding_count = len(scan_result.findings)
            await self._runner.emit_progress(child_id, f"{finding_count} findings found")

            # Persist via mapper — pass child run_id to skip run creation
            target_id = self._config.get("target_id")
            persist_scan(
                scan_result,
                db_path=self._runner._db_path,
                transport=self._config["transport"],
                run_id=child_id,
                target_id=target_id,
            )

            # Emit finding events
            for finding in scan_result.findings:
                core_sev = _map_severity(finding.severity)
                await self._runner.emit_finding(
                    finding_id=finding.rule_id,
                    run_id=child_id,
                    module="audit",
                    severity=int(core_sev),
                    title=finding.title,
                )

            await self._runner.update_child_status(child_id, RunStatus.COMPLETED)
            return AuditResult(
                run_id=child_id,
                scan_result=scan_result,
                finding_count=finding_count,
            )

        except Exception:
            await self._runner.update_child_status(child_id, RunStatus.FAILED)
            raise
