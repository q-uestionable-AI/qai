"""Audit report AI-evaluation prompt builder."""

from __future__ import annotations

from typing import Any


def build_audit_interpret_prompt(scan_result: Any) -> str:
    """Assemble an AI-evaluation prompt from audit scan results.

    The prompt is findings-driven only: targets, techniques, results.
    Tool identity is excluded.

    Args:
        scan_result: A ScanResult or duck-typed equivalent.

    Returns:
        Prompt string ready for embedding in reports.
    """
    server_info = getattr(scan_result, "server_info", {}) or {}
    server_name = server_info.get("name", "unknown target")
    tools_scanned = getattr(scan_result, "tools_scanned", 0)
    scanners_run = getattr(scan_result, "scanners_run", []) or []
    findings = getattr(scan_result, "findings", []) or []

    counts: dict[str, int] = {}
    categories: list[str] = []
    for f in findings:
        key = f.severity.value
        counts[key] = counts.get(key, 0) + 1
        cat = f.category
        if cat not in categories:
            categories.append(cat)

    techniques_str = ", ".join(scanners_run) if scanners_run else "standard MCP security scanners"
    tool_count = tools_scanned or 0
    target_str = f"{tool_count} MCP tool{'s' if tool_count != 1 else ''} on {server_name}"

    if not findings:
        return (
            f"{target_str} scanned for MCP security vulnerabilities "
            f"using {techniques_str}. "
            "No findings identified. "
            "Confirm scan coverage was adequate and note any scan errors."
        )

    parts = []
    for sev in ["critical", "high", "medium", "low", "info"]:
        n = counts.get(sev, 0)
        if n:
            parts.append(f"{n} {sev}")
    finding_str = (
        f"{len(findings)} finding{'s' if len(findings) != 1 else ''} identified: "
        f"{', '.join(parts)}."
    )
    if categories:
        owasp_ids: list[str] = []
        for f in findings:
            fwids = getattr(f, "framework_ids", {}) or {}
            owasp_id = fwids.get("owasp_mcp_top10", "")
            if owasp_id and owasp_id not in owasp_ids:
                owasp_ids.append(owasp_id)
        if owasp_ids:
            finding_str += f" OWASP categories: {', '.join(owasp_ids)}."

    return (
        f"{target_str} scanned for MCP security vulnerabilities "
        f"using {techniques_str}. "
        f"{finding_str} "
        "Prioritize findings by exploitability and recommend red team sequencing."
    )
