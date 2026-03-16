"""HTML report output for scan results.

Generates a self-contained HTML report with inline CSS, dark theme,
severity-colored badges, expandable finding cards, and print-friendly
styles. No external dependencies (CSS, JS, fonts).
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from q_ai import __version__
from q_ai.audit.reporting.prompt import build_audit_interpret_prompt
from q_ai.mcp.models import ScanFinding, Severity

_REPO_URL = "https://github.com/q-uestionable-AI/qai"

_SEVERITY_COLORS: dict[str, str] = {
    "critical": "#ef4444",
    "high": "#f97316",
    "medium": "#eab308",
    "low": "#3b82f6",
    "info": "#6b7280",
}

_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def _esc(value: Any) -> str:
    """HTML-escape a value, converting to string first.

    Args:
        value: Any value to escape for safe HTML embedding.

    Returns:
        HTML-escaped string.
    """
    return html.escape(str(value))


def _format_duration(
    started_at: datetime | None,
    finished_at: datetime | None,
) -> str:
    """Format scan duration as a human-readable string.

    Args:
        started_at: Scan start time.
        finished_at: Scan finish time.

    Returns:
        Duration string like '5m 12s' or 'N/A'.
    """
    if started_at is None or finished_at is None:
        return "N/A"
    delta = finished_at - started_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}m {seconds}s"


def _count_by_severity(findings: list[ScanFinding]) -> dict[str, int]:
    """Count findings grouped by severity level.

    Args:
        findings: List of ScanFinding objects.

    Returns:
        Dict mapping severity value strings to counts.
    """
    counts: dict[str, int] = {}
    for f in findings:
        key = f.severity.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _severity_badge(severity_value: str) -> str:
    """Generate an HTML severity badge span.

    Args:
        severity_value: Severity string (e.g., 'critical', 'high').

    Returns:
        HTML span element with inline color styling.
    """
    color = _SEVERITY_COLORS.get(severity_value, "#6b7280")
    label = _esc(severity_value.upper())
    return (
        f'<span class="badge severity-{_esc(severity_value)}" '
        f'style="background:{color}">{label}</span>'
    )


def _summary_badges(findings: list[ScanFinding]) -> str:
    """Generate the summary bar HTML with severity counts.

    Args:
        findings: List of ScanFinding objects.

    Returns:
        HTML string for the summary badges section.
    """
    counts = _count_by_severity(findings)
    parts = [f'<span class="summary-total">{len(findings)} findings</span>']
    for sev in Severity:
        count = counts.get(sev.value, 0)
        color = _SEVERITY_COLORS.get(sev.value, "#6b7280")
        parts.append(
            f'<span class="summary-badge" style="border-color:{color};">'
            f'<span class="summary-dot" style="background:{color};"></span>'
            f"{_esc(sev.value.upper())} {count}</span>"
        )
    return "\n".join(parts)


def _findings_table(findings: list[ScanFinding]) -> str:
    """Generate the findings summary table HTML.

    Args:
        findings: List of ScanFinding objects, sorted by severity.

    Returns:
        HTML string for the findings table.
    """
    if not findings:
        return '<p class="no-findings">No findings detected.</p>'

    rows: list[str] = [
        "<tr>"
        f"<td>{_severity_badge(f.severity.value)}</td>"
        f"<td>{_esc(f.category)}</td>"
        f"<td>{_esc(f.rule_id)}</td>"
        f"<td>{_esc(f.title)}</td>"
        f"<td>{_esc(f.tool_name or 'N/A')}</td>"
        "</tr>"
        for f in findings
    ]

    return (
        '<table class="findings-table">'
        "<thead><tr>"
        "<th>Severity</th><th>Category</th><th>Rule ID</th>"
        "<th>Title</th><th>Tool</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _finding_card(finding: ScanFinding) -> str:
    """Generate an expandable detail card for a single finding.

    Args:
        finding: A ScanFinding object.

    Returns:
        HTML string for the finding detail card using details/summary.
    """
    sev = finding.severity.value
    color = _SEVERITY_COLORS.get(sev, "#6b7280")
    meta_items = "".join(
        f"<li><strong>{_esc(k)}:</strong> {_esc(v)}</li>" for k, v in finding.metadata.items()
    )
    meta_section = (
        f'<div class="card-section"><h4>Metadata</h4><ul>{meta_items}</ul></div>'
        if finding.metadata
        else ""
    )

    return (
        f'<details class="finding-card" style="border-left-color:{color};">'
        f"<summary>"
        f"{_severity_badge(sev)} "
        f'<span class="card-title">{_esc(finding.title)}</span>'
        f'<span class="card-ids">{_esc(finding.category)} / '
        f"{_esc(finding.rule_id)}</span>"
        f"</summary>"
        f'<div class="card-body">'
        f'<div class="card-section">'
        f"<h4>Description</h4><p>{_esc(finding.description)}</p></div>"
        f'<div class="card-section"><h4>Evidence</h4>'
        f"<pre>{_esc(finding.evidence)}</pre></div>"
        f'<div class="card-section"><h4>Remediation</h4>'
        f"<p>{_esc(finding.remediation)}</p></div>"
        f'<div class="card-section"><h4>Tool</h4>'
        f"<p>{_esc(finding.tool_name or 'N/A')}</p></div>"
        f"{meta_section}"
        f'<div class="card-section"><h4>Timestamp</h4>'
        f"<p>{_esc(finding.timestamp.isoformat())}</p></div>"
        f"</div>"
        f"</details>"
    )


def _errors_section(errors: list[dict[str, str]]) -> str:
    """Generate the errors section HTML.

    Args:
        errors: List of error dicts with 'scanner' and 'error' keys.

    Returns:
        HTML string for the errors section, or empty string if no errors.
    """
    if not errors:
        return ""

    items = "".join(
        f"<li><strong>{_esc(e.get('scanner', 'unknown'))}:</strong> {_esc(e.get('error', ''))}</li>"
        for e in errors
    )
    return f'<div class="errors-section"><h2>Errors ({len(errors)})</h2><ul>{items}</ul></div>'


def _css() -> str:
    """Return the inline CSS stylesheet.

    Returns:
        CSS string for the HTML report.
    """
    return """
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#0f172a;color:#e2e8f0;line-height:1.6;padding:2rem;
  max-width:1200px;margin:0 auto;
}
a{color:#06b6d4;text-decoration:none}
a:hover{text-decoration:underline}
h1,h2,h3{color:#f1f5f9}
.header{
  border-bottom:2px solid #06b6d4;padding-bottom:1rem;margin-bottom:2rem;
}
.header h1{font-size:1.8rem;color:#06b6d4}
.header .subtitle{color:#94a3b8;font-size:0.9rem;margin-top:0.25rem}
.summary-bar{
  display:flex;flex-wrap:wrap;gap:0.75rem;align-items:center;
  margin-bottom:2rem;padding:1rem;background:#1e293b;border-radius:8px;
}
.summary-total{
  font-size:1.4rem;font-weight:700;color:#f1f5f9;margin-right:0.5rem;
}
.summary-badge{
  display:inline-flex;align-items:center;gap:0.35rem;
  padding:0.25rem 0.75rem;border:1px solid;border-radius:9999px;
  font-size:0.8rem;font-weight:600;color:#e2e8f0;
}
.summary-dot{
  width:8px;height:8px;border-radius:50%;display:inline-block;
}
.metadata-grid{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:1rem;margin-bottom:2rem;padding:1rem;background:#1e293b;border-radius:8px;
}
.metadata-item{font-size:0.85rem}
.metadata-item .label{color:#94a3b8;font-size:0.75rem;text-transform:uppercase}
.metadata-item .value{color:#f1f5f9;font-weight:600}
.findings-table{
  width:100%;border-collapse:collapse;margin-bottom:2rem;
}
.findings-table th{
  text-align:left;padding:0.75rem;background:#1e293b;color:#94a3b8;
  font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;
  border-bottom:1px solid #334155;
}
.findings-table td{
  padding:0.75rem;border-bottom:1px solid #1e293b;font-size:0.85rem;
}
.findings-table tr:hover{background:#1e293b}
.badge{
  display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;
  font-size:0.7rem;font-weight:700;color:#fff;text-transform:uppercase;
}
.no-findings{
  text-align:center;padding:2rem;color:#94a3b8;font-style:italic;
}
h2.section-title{margin:2rem 0 1rem;color:#06b6d4;font-size:1.2rem}
.finding-card{
  background:#1e293b;border-radius:8px;margin-bottom:0.75rem;
  border-left:4px solid #06b6d4;overflow:hidden;
}
.finding-card summary{
  padding:0.75rem 1rem;cursor:pointer;display:flex;
  align-items:center;gap:0.75rem;font-size:0.9rem;
}
.finding-card summary:hover{background:#263349}
.finding-card summary::marker{color:#06b6d4}
.card-title{font-weight:600;color:#f1f5f9;flex:1}
.card-ids{color:#94a3b8;font-size:0.75rem;white-space:nowrap}
.card-body{padding:0 1rem 1rem}
.card-section{margin-top:0.75rem}
.card-section h4{
  color:#06b6d4;font-size:0.75rem;text-transform:uppercase;
  margin-bottom:0.25rem;
}
.card-section p{color:#cbd5e1;font-size:0.85rem}
.card-section pre{
  background:#0f172a;padding:0.75rem;border-radius:4px;
  font-size:0.8rem;overflow-x:auto;color:#e2e8f0;white-space:pre-wrap;
}
.card-section ul{list-style:disc;padding-left:1.5rem;color:#cbd5e1;font-size:0.85rem}
.errors-section{
  background:#451a03;border:1px solid #92400e;border-radius:8px;
  padding:1rem;margin-top:2rem;
}
.errors-section h2{color:#fbbf24;font-size:1rem;margin-bottom:0.5rem}
.errors-section ul{list-style:disc;padding-left:1.5rem}
.errors-section li{color:#fde68a;font-size:0.85rem;margin-bottom:0.25rem}
.footer{
  margin-top:3rem;padding-top:1rem;border-top:1px solid #334155;
  text-align:center;color:#64748b;font-size:0.8rem;
}
.analyst-context{
  background:#1e293b;border-radius:8px;margin-bottom:2rem;
  border-left:4px solid #06b6d4;
}
.analyst-context summary{
  padding:0.75rem 1rem;cursor:pointer;color:#06b6d4;
  font-size:0.85rem;font-weight:600;list-style:none;
}
.analyst-context summary:hover{background:#263349}
.analyst-context .context-body{padding:0 1rem 1rem}
.analyst-context .context-body p{
  color:#94a3b8;font-size:0.8rem;margin-bottom:0.5rem;margin-top:0.5rem;
}
.analyst-context pre{
  background:#0f172a;padding:0.75rem;border-radius:4px;
  font-size:0.8rem;color:#e2e8f0;white-space:pre-wrap;word-break:break-word;
}
@media print{
  body{background:#fff;color:#1e293b;padding:1rem}
  .header h1{color:#0e7490}
  h1,h2,h3,h4{color:#1e293b}
  .summary-bar,.metadata-grid,.finding-card{
    background:#f8fafc;border:1px solid #e2e8f0;
  }
  .findings-table th{background:#f1f5f9;color:#475569}
  .findings-table td{border-bottom-color:#e2e8f0}
  .card-section pre{background:#f1f5f9;color:#1e293b}
  .errors-section{background:#fef3c7;border-color:#f59e0b}
  .errors-section h2{color:#92400e}
  .errors-section li{color:#78350f}
  .footer{color:#94a3b8}
  details.finding-card:not([open])>:not(summary){display:block!important}
  details.finding-card>summary{display:list-item}
  details.finding-card>.card-body{display:block!important}
}
@media(max-width:768px){
  body{padding:1rem}
  .metadata-grid{grid-template-columns:1fr 1fr}
  .findings-table{font-size:0.75rem}
  .findings-table th,.findings-table td{padding:0.5rem}
}
"""


def generate_html_report(scan_result: Any, output_path: str | Path) -> Path:
    """Generate a self-contained HTML report from scan results.

    Produces a single HTML file with inline CSS, no external dependencies.
    Dark theme with cyan accents, expandable finding cards, severity badges,
    and print-friendly styles.

    Args:
        scan_result: A ScanResult from the orchestrator (or any duck-typed
            object with findings, server_info, tools_scanned, scanners_run,
            started_at, finished_at, errors attributes).
        output_path: File path to write the HTML report.

    Returns:
        Path to the written report file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    qai_version = __version__
    server_info = getattr(scan_result, "server_info", {}) or {}
    server_name = server_info.get("name", "Unknown Server")
    protocol_version = server_info.get("protocolVersion", "N/A")
    transport_type = server_info.get("transport", "N/A")
    tools_scanned = getattr(scan_result, "tools_scanned", 0)
    scanners_run = getattr(scan_result, "scanners_run", [])
    started_at = getattr(scan_result, "started_at", None)
    finished_at = getattr(scan_result, "finished_at", None)
    findings: list[ScanFinding] = getattr(scan_result, "findings", [])
    errors: list[dict[str, str]] = getattr(scan_result, "errors", [])

    scan_date = started_at.strftime("%Y-%m-%d %H:%M:%S UTC") if started_at else "N/A"
    duration = _format_duration(started_at, finished_at)

    sorted_findings = sorted(
        findings,
        key=lambda f: _SEVERITY_ORDER.get(f.severity.value, 99),
    )

    finding_cards = "\n".join(_finding_card(f) for f in sorted_findings)

    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>q-ai Scan Report — {_esc(server_name)}</title>\n"
        f"<style>{_css()}</style>\n"
        "</head>\n<body>\n"
        # Header
        '<div class="header">\n'
        "<h1>q-ai Scan Report</h1>\n"
        f'<div class="subtitle">{_esc(server_name)} &mdash; '
        f"Protocol {_esc(protocol_version)} &mdash; {_esc(scan_date)}</div>\n"
        "</div>\n"
        # Summary bar
        f'<div class="summary-bar">\n{_summary_badges(findings)}\n</div>\n'
        # Metadata grid
        '<div class="metadata-grid">\n'
        f'<div class="metadata-item">'
        f'<div class="label">Transport</div>'
        f'<div class="value">{_esc(transport_type)}</div></div>\n'
        f'<div class="metadata-item">'
        f'<div class="label">Scanners Run</div>'
        f'<div class="value">'
        f"{_esc(', '.join(scanners_run) if scanners_run else 'N/A')}"
        f"</div></div>\n"
        f'<div class="metadata-item">'
        f'<div class="label">Tools Scanned</div>'
        f'<div class="value">{_esc(str(tools_scanned))}</div></div>\n'
        f'<div class="metadata-item">'
        f'<div class="label">Duration</div>'
        f'<div class="value">{_esc(duration)}</div></div>\n'
        f'<div class="metadata-item">'
        f'<div class="label">q-ai Version</div>'
        f'<div class="value">{_esc(qai_version)}</div></div>\n'
        "</div>\n"
        # Analyst context prompt
        '<details class="analyst-context">\n'
        "<summary>Analyst Context — AI Evaluation Prompt</summary>\n"
        '<div class="context-body">\n'
        "<p>Copy this prompt into any AI assistant to interpret these findings.</p>\n"
        f"<pre>{_esc(build_audit_interpret_prompt(scan_result))}</pre>\n"
        "</div>\n"
        "</details>\n"
        # Findings table
        '<h2 class="section-title">Findings Summary</h2>\n'
        f"{_findings_table(sorted_findings)}\n"
        # Finding detail cards
        '<h2 class="section-title">Finding Details</h2>\n'
        f"{finding_cards}\n"
        # Errors section
        f"{_errors_section(errors)}\n"
        # Footer
        '<div class="footer">\n'
        f"Generated by q-ai {_esc(qai_version)} &mdash; "
        f'<a href="{_REPO_URL}">{_REPO_URL}</a>\n'
        "</div>\n"
        "</body>\n</html>"
    )

    output_path.write_text(doc, encoding="utf-8")
    return output_path
