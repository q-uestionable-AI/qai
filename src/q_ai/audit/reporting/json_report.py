"""JSON report output for scan results.

Serializes ScanResult into a structured JSON document suitable
for programmatic consumption, CI/CD integration, and as input
to the report command for generating HTML/SARIF.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from q_ai import __version__
from q_ai.audit.reporting.prompt import build_audit_interpret_prompt
from q_ai.mcp.models import ScanFinding, Severity


def finding_to_dict(finding: ScanFinding) -> dict[str, Any]:
    """Convert a ScanFinding to a JSON-serializable dict.

    Args:
        finding: A ScanFinding object from a scanner.

    Returns:
        Dict representation of the finding.
    """
    return {
        "rule_id": finding.rule_id,
        "category": finding.category,
        "framework_ids": finding.framework_ids,
        "owasp_id": finding.framework_ids.get("owasp_mcp_top10", ""),
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity.value,
        "evidence": finding.evidence,
        "remediation": finding.remediation,
        "tool_name": finding.tool_name,
        "metadata": finding.metadata,
        "timestamp": finding.timestamp.isoformat(),
    }


def dict_to_finding(data: dict[str, Any]) -> ScanFinding:
    """Reconstruct a ScanFinding from a JSON-serialized dict.

    Inverse of finding_to_dict. Used by the report command to
    load findings from saved JSON scan results.

    Args:
        data: Dict as produced by finding_to_dict.

    Returns:
        Reconstructed ScanFinding object.
    """
    return ScanFinding(
        rule_id=data["rule_id"],
        category=data.get("category", ""),
        title=data["title"],
        description=data["description"],
        severity=Severity(data["severity"]),
        evidence=data.get("evidence", ""),
        remediation=data.get("remediation", ""),
        tool_name=data.get("tool_name", ""),
        metadata=data.get("metadata", {}),
        timestamp=datetime.fromisoformat(data["timestamp"])
        if "timestamp" in data
        else datetime.now(UTC),
        framework_ids=data.get("framework_ids", {}),
    )


def generate_json_report(scan_result: Any, output_path: str | Path) -> Path:
    """Generate a JSON report from scan results.

    Args:
        scan_result: A ScanResult from the orchestrator.
        output_path: File path to write the JSON report.

    Returns:
        Path to the written report file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "prompt": build_audit_interpret_prompt(scan_result),
        "version": __version__,
        "scan": {
            "server": scan_result.server_info,
            "tools_scanned": scan_result.tools_scanned,
            "scanners_run": scan_result.scanners_run,
            "started_at": scan_result.started_at.isoformat(),
            "finished_at": (
                scan_result.finished_at.isoformat() if scan_result.finished_at else None
            ),
        },
        "summary": {
            "total_findings": len(scan_result.findings),
            "by_severity": _count_by_severity(scan_result.findings),
            "errors": len(scan_result.errors),
        },
        "findings": [finding_to_dict(f) for f in scan_result.findings],
        "errors": scan_result.errors,
    }

    output_path.write_text(json.dumps(report, indent=2, default=str))
    return output_path


def _count_by_severity(findings: list[ScanFinding]) -> dict[str, int]:
    """Count findings grouped by severity level."""
    counts: dict[str, int] = {}
    for f in findings:
        key = f.severity.value
        counts[key] = counts.get(key, 0) + 1
    return counts
