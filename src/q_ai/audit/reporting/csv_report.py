"""CSV report output for scan results.

Flat one-row-per-finding export for spreadsheets and analysis tools.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from q_ai.core.mitigation import SourceType

CSV_COLUMNS = [
    "run_id",
    "target_name",
    "category",
    "severity",
    "title",
    "description",
    "tool_name",
    "owasp_mcp_id",
    "remediation",
    "mitigation_summary",
    "framework_ids",
    "timestamp",
]


def _mitigation_summary(mitigation: Any) -> str:
    """Condense mitigation guidance to a single CSV-friendly string.

    Args:
        mitigation: A MitigationGuidance object, dict, or None.

    Returns:
        Summary string: first taxonomy action, 'See full report', or empty.
    """
    if mitigation is None:
        return ""

    sections = getattr(mitigation, "sections", None)
    if sections is None and isinstance(mitigation, dict):
        sections = mitigation.get("sections", [])

    if not sections:
        return ""

    if len(sections) == 1:
        section = sections[0]
        source_type = getattr(section, "source_type", None)
        if source_type is None and isinstance(section, dict):
            source_type = section.get("source_type")
        items = getattr(section, "items", None)
        if items is None and isinstance(section, dict):
            items = section.get("items", [])
        if str(source_type) == str(SourceType.TAXONOMY) and items:
            first: str = str(items[0])
            return first

    return "See full report for details"


def _finding_to_row(finding: Any, meta: dict[str, Any]) -> dict[str, str]:
    """Convert a finding to a flat CSV row dict.

    Args:
        finding: A ScanFinding (mcp.models) or Finding (core.models).
        meta: Run metadata dict with run_id, target_name.

    Returns:
        Dict mapping column names to string values.
    """
    is_scan_finding = hasattr(finding, "rule_id")

    framework_ids = finding.framework_ids if finding.framework_ids else {}
    owasp_id = ""
    if isinstance(framework_ids, dict):
        owasp_id = framework_ids.get("owasp_mcp_top10", "")
        if isinstance(owasp_id, list):
            owasp_id = ", ".join(owasp_id)

    severity = finding.severity
    severity_str = severity.value if hasattr(severity, "value") else str(severity)

    mitigation = getattr(finding, "mitigation", None)

    timestamp = ""
    if is_scan_finding and hasattr(finding, "timestamp"):
        timestamp = finding.timestamp.isoformat()
    elif hasattr(finding, "created_at") and finding.created_at:
        timestamp = finding.created_at.isoformat()

    fw_str = ""
    if framework_ids:
        parts = []
        for k, v in framework_ids.items():
            if isinstance(v, list):
                parts.append(f"{k}={','.join(v)}")
            else:
                parts.append(f"{k}={v}")
        fw_str = "; ".join(parts)

    return {
        "run_id": meta.get("run_id", ""),
        "target_name": meta.get("target_name", ""),
        "category": finding.category,
        "severity": severity_str,
        "title": finding.title,
        "description": getattr(finding, "description", "") or "",
        "tool_name": getattr(finding, "tool_name", "") or "",
        "owasp_mcp_id": owasp_id,
        "remediation": getattr(finding, "remediation", "") or "",
        "mitigation_summary": _mitigation_summary(mitigation),
        "framework_ids": fw_str,
        "timestamp": timestamp,
    }


def generate_csv_report(
    scan_result: Any,
    output_path: str | Path,
    run_metadata: dict[str, Any] | None = None,
) -> Path:
    """Generate a CSV report from scan results.

    Works with both ScanResult objects (CLI path) and
    list[Finding] (web UI export path).

    Args:
        scan_result: A ScanResult or list of Finding objects.
        output_path: File path to write the CSV output.
        run_metadata: Optional dict with run_id, target_name.

    Returns:
        Path to the written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    findings = scan_result.findings if hasattr(scan_result, "findings") else scan_result
    meta = run_metadata or {}

    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for finding in findings:
            writer.writerow(_finding_to_row(finding, meta))

    return output_path
