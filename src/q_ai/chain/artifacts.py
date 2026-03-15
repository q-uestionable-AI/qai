"""Artifact extraction for chain step outputs.

Extracts standard named artifacts from audit ScanResult and inject Campaign
objects using duck typing (getattr with defaults) to avoid circular imports.
"""

from __future__ import annotations

from typing import Any

# Severity ordering for audit findings (highest to lowest)
_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def extract_audit_artifacts(scan_result: Any) -> dict[str, str]:
    """Extract standard artifacts from an audit ScanResult.

    Always produces the same keys:
    - vulnerable_tool: tool_name from highest-severity finding, or ""
    - vulnerability_type: owasp_id from highest-severity finding, or ""
    - finding_count: number of findings as string
    - finding_evidence: evidence from highest-severity finding, or ""

    Args:
        scan_result: A ScanResult-like object with a ``findings`` attribute.

    Returns:
        Dict of standard artifact key-value pairs.
    """
    findings = getattr(scan_result, "findings", []) or []

    if not findings:
        return {
            "vulnerable_tool": "",
            "vulnerability_type": "",
            "finding_count": "0",
            "finding_evidence": "",
        }

    # Sort by severity (stable sort preserves original order for ties)
    sorted_findings = sorted(
        findings,
        key=lambda f: _SEVERITY_ORDER.get(str(getattr(f, "severity", "info")).lower(), 999),
    )

    top = sorted_findings[0]
    return {
        "vulnerable_tool": str(getattr(top, "tool_name", "") or ""),
        "vulnerability_type": str(getattr(top, "owasp_id", "") or ""),
        "finding_count": str(len(findings)),
        "finding_evidence": str(getattr(top, "evidence", "") or ""),
    }


# Outcome ordering for inject results (best to worst)
_OUTCOME_ORDER = {
    "full_compliance": 0,
    "partial_compliance": 1,
    "refusal_with_leak": 2,
    "clean_refusal": 3,
    "error": 4,
}

_SUCCESS_OUTCOMES = {"full_compliance", "partial_compliance"}


def extract_inject_artifacts(campaign: Any) -> dict[str, str]:
    """Extract standard artifacts from an inject Campaign.

    Always produces the same keys:
    - best_outcome: highest compliance level achieved, or ""
    - working_payload: payload_name of first successful result, or ""
    - working_technique: technique of first successful result, or ""
    - compliance_rate: percentage achieving full or partial compliance as string, or "0"

    Args:
        campaign: A Campaign-like object with a ``results`` attribute.

    Returns:
        Dict of standard artifact key-value pairs.
    """
    results = getattr(campaign, "results", []) or []

    if not results:
        return {
            "best_outcome": "",
            "working_payload": "",
            "working_technique": "",
            "compliance_rate": "0",
        }

    # Find first successful result (full or partial compliance)
    first_success = None
    for r in results:
        outcome = str(getattr(r, "outcome", ""))
        if outcome in _SUCCESS_OUTCOMES:
            first_success = r
            break

    # Find best outcome across all results
    best = min(
        results,
        key=lambda r: _OUTCOME_ORDER.get(str(getattr(r, "outcome", "")), 999),
    )
    best_outcome_str = str(getattr(best, "outcome", ""))

    # Only report best_outcome if it's a recognized compliance level
    if best_outcome_str not in _SUCCESS_OUTCOMES:
        best_outcome_str = ""

    # Compliance rate
    compliant_count = sum(1 for r in results if str(getattr(r, "outcome", "")) in _SUCCESS_OUTCOMES)
    compliance_rate = str(round(compliant_count / len(results) * 100))

    # Extract working payload/technique from first successful result, guarding against None
    working_payload = ""
    working_technique = ""
    if first_success:
        working_payload = str(getattr(first_success, "payload_name", None) or "")
        working_technique = str(getattr(first_success, "technique", None) or "")

    return {
        "best_outcome": best_outcome_str,
        "working_payload": working_payload,
        "working_technique": working_technique,
        "compliance_rate": compliance_rate,
    }
