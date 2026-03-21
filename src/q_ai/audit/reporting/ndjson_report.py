"""NDJSON report output for scan results.

Streaming-friendly export: one JSON object per line.
Each line is self-contained with finding data and optional run metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from q_ai.audit.reporting.json_report import finding_to_dict


def _finding_obj_to_dict(finding: Any) -> dict[str, Any]:
    """Convert a finding to a dict, handling both ScanFinding and core Finding.

    Args:
        finding: A ScanFinding (mcp.models) or Finding (core.models).

    Returns:
        Dict representation of the finding.
    """
    if hasattr(finding, "rule_id"):
        return finding_to_dict(finding)
    severity = finding.severity
    severity_str = severity.name.lower() if hasattr(severity, "name") else str(severity)
    return {
        "category": finding.category,
        "severity": severity_str,
        "title": finding.title,
        "description": finding.description,
        "framework_ids": finding.framework_ids,
        "mitigation": finding.mitigation,
        "source_ref": getattr(finding, "source_ref", None),
        "created_at": (
            finding.created_at.isoformat() if getattr(finding, "created_at", None) else None
        ),
    }


def generate_ndjson_report(
    scan_result: Any,
    output_path: str | Path,
    run_metadata: dict[str, Any] | None = None,
) -> Path:
    """Generate an NDJSON report from scan results.

    Each line is a self-contained JSON object. Works with both ScanResult
    objects (CLI path) and list[Finding] (web UI export path).

    Args:
        scan_result: A ScanResult or list of Finding objects.
        output_path: File path to write the NDJSON output.
        run_metadata: Optional dict with run_id, started_at, target_name.

    Returns:
        Path to the written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    findings = scan_result.findings if hasattr(scan_result, "findings") else scan_result
    meta = run_metadata or {}

    lines: list[str] = []
    for finding in findings:
        obj = _finding_obj_to_dict(finding)
        obj.update(meta)
        lines.append(json.dumps(obj, default=str))

    output_path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return output_path
