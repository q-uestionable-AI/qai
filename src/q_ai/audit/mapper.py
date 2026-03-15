"""Mapper from ScanFinding to core Finding for DB persistence.

Bridges the audit scanner domain (ScanFinding) to the core persistence
domain (Finding, Run, Target) so scan results can be stored in the
unified SQLite database.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from q_ai.core.db import get_connection
from q_ai.core.models import Severity as CoreSeverity
from q_ai.mcp.models import ScanFinding, Severity

if TYPE_CHECKING:
    from q_ai.audit.orchestrator import ScanResult


def _map_severity(severity: Severity) -> CoreSeverity:
    """Convert a scanner StrEnum severity to a core IntEnum severity.

    Args:
        severity: Scanner severity (StrEnum).

    Returns:
        Core severity (IntEnum) with the same name.
    """
    return CoreSeverity[severity.name]


def _build_description(finding: ScanFinding) -> str:
    """Combine ScanFinding fields into a single description string.

    Args:
        finding: A ScanFinding from a scanner module.

    Returns:
        Combined description with tool, evidence, and remediation context.
    """
    parts: list[str] = []
    if finding.description:
        parts.append(finding.description)
    if finding.tool_name:
        parts.append(f"Tool: {finding.tool_name}")
    if finding.evidence:
        parts.append(f"Evidence: {finding.evidence}")
    if finding.remediation:
        parts.append(f"Remediation: {finding.remediation}")
    return "\n\n".join(parts)


def _create_audit_scan(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    transport: str,
    server_name: str | None,
    server_version: str | None,
    scanners_run: list[str],
    finding_count: int,
    scan_duration_seconds: float | None,
) -> str:
    """Insert a row into the audit_scans table.

    Args:
        conn: Active database connection.
        run_id: Associated run ID.
        transport: Transport type used for the scan.
        server_name: MCP server name from handshake.
        server_version: MCP server version from handshake.
        scanners_run: List of scanner names that were executed.
        finding_count: Total number of findings produced.
        scan_duration_seconds: Scan duration in seconds, or None.

    Returns:
        The hex UUID of the newly created audit_scans row.
    """
    audit_scan_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO audit_scans
            (id, run_id, transport, server_name, server_version,
             scanners_run, finding_count, scan_duration_seconds, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_scan_id,
            run_id,
            transport,
            server_name,
            server_version,
            json.dumps(scanners_run),
            finding_count,
            scan_duration_seconds,
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )
    return audit_scan_id


def persist_scan(
    scan_result: ScanResult,
    db_path: Path | None = None,
    transport: str = "stdio",
    run_id: str | None = None,
    target_id: str | None = None,
) -> str:
    """Persist a ScanResult to the database.

    Creates a target, run, findings, and audit_scan row for the given
    scan result. Returns the run ID.

    Args:
        scan_result: Complete scan result from the orchestrator.
        db_path: Path to database file. Defaults to ~/.qai/qai.db.
        transport: Transport type used for the scan.
        run_id: Optional pre-created run ID from the orchestrator.
            When provided, skips creating a new run row.
        target_id: Optional pre-created target ID from the orchestrator.
            When provided, skips creating a new target row.

    Returns:
        The run ID for the persisted scan.
    """
    server_info = scan_result.server_info or {}
    server_name = server_info.get("name", "unknown")
    server_version = server_info.get("version", "unknown")

    # Compute scan duration
    scan_duration: float | None = None
    if scan_result.started_at and scan_result.finished_at:
        delta = scan_result.finished_at - scan_result.started_at
        scan_duration = delta.total_seconds()

    with get_connection(db_path) as conn:
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()

        # Create target (unless pre-created by orchestrator)
        if target_id is None:
            target_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO targets (id, type, name, uri, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target_id, "server", server_name, None, json.dumps(server_info), now_iso),
            )

        # Create run (unless pre-created by orchestrator)
        if run_id is None:
            run_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO runs
                    (id, module, name, target_id, parent_run_id,
                     config, status, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "audit",
                    f"audit-{server_name}",
                    target_id,
                    None,
                    None,
                    2,  # RunStatus.COMPLETED
                    scan_result.started_at.isoformat() if scan_result.started_at else now_iso,
                    scan_result.finished_at.isoformat() if scan_result.finished_at else now_iso,
                ),
            )

        # Create findings
        for finding in scan_result.findings:
            finding_id = uuid.uuid4().hex
            core_severity = _map_severity(finding.severity)
            description = _build_description(finding)
            framework_ids = finding.framework_ids if finding.framework_ids else None

            conn.execute(
                """
                INSERT INTO findings
                    (id, run_id, module, category, severity,
                     title, description, framework_ids,
                     source_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id,
                    run_id,
                    "audit",
                    finding.category,
                    int(core_severity),
                    finding.title,
                    description,
                    json.dumps(framework_ids) if framework_ids else None,
                    finding.tool_name or None,
                    finding.timestamp.isoformat(),
                ),
            )

        # Create audit_scans row
        _create_audit_scan(
            conn,
            run_id=run_id,
            transport=transport,
            server_name=server_name,
            server_version=server_version,
            scanners_run=scan_result.scanners_run,
            finding_count=len(scan_result.findings),
            scan_duration_seconds=scan_duration,
        )

    return run_id
