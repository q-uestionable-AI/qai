"""Blast radius analysis for completed chain executions.

Reads a ChainResult and produces an attack path analysis answering:
what did the attacker reach? Outputs JSON or HTML reports.
"""

from __future__ import annotations

import html
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

_REPO_URL = "https://github.com/q-uestionable-AI/qai"


def analyze_blast_radius(result: dict[str, Any]) -> dict[str, Any]:
    """Analyze the blast radius from a completed chain execution.

    Walks step_outputs and extracts data reached, systems touched,
    trust boundaries crossed, and the ordered attack path.

    Args:
        result: A chain result dict (as produced by ChainResult.to_dict()
            or loaded from a chain run JSON report).

    Returns:
        A blast radius analysis dict.
    """
    step_outputs = result.get("step_outputs", [])
    steps_from_tracer = result.get("steps", [])
    target_config = result.get("target_config", {})
    trust_boundaries = result.get("trust_boundaries_crossed", [])

    # Use step_outputs for live runs, fall back to tracer steps for dry-runs
    steps_to_analyze = step_outputs if step_outputs else steps_from_tracer

    data_reached: list[dict[str, str]] = []
    attack_path: list[dict[str, Any]] = []
    systems_touched: list[dict[str, str]] = []

    # Track which transports we've already recorded
    seen_transports: set[str] = set()

    for step in steps_to_analyze:
        step_id = step.get("step_id") or step.get("id", "")
        module = step.get("module", "unknown")
        technique = step.get("technique", "")
        # Default success=True for tracer steps (trace_chain follows success path)
        success = step.get("success", True)

        # For tracer steps, infer success from status if present
        if "status" in step:
            success = step.get("status") == "success"

        # Build attack path entry
        attack_path.append(
            {
                "step": step_id,
                "module": module,
                "technique": technique,
                "success": success,
            }
        )

        # Extract data_reached from artifacts
        artifacts = step.get("artifacts", {})
        for key, value in artifacts.items():
            if value and str(value) not in ("", "0"):
                data_reached.append(
                    {
                        "type": key,
                        "value": str(value),
                        "source_step": step_id,
                    }
                )

        # Extract from enriched fields (total_findings, outcome, payload_name)
        if step.get("total_findings"):
            data_reached.append(
                {
                    "type": "total_findings",
                    "value": str(step["total_findings"]),
                    "source_step": step_id,
                }
            )
        if step.get("outcome"):
            data_reached.append(
                {
                    "type": "outcome",
                    "value": str(step["outcome"]),
                    "source_step": step_id,
                }
            )
        if step.get("payload_name"):
            data_reached.append(
                {
                    "type": "payload_name",
                    "value": str(step["payload_name"]),
                    "source_step": step_id,
                }
            )

        # Systems touched — derive from target_config per step module
        transport_key = f"{module}:{step_id}"
        if transport_key not in seen_transports:
            seen_transports.add(transport_key)
            transport = ""
            if module == "audit":
                transport = target_config.get("audit_transport", "") or ""
            elif module == "inject":
                transport = target_config.get("inject_model", "") or ""
            entry: dict[str, str] = {
                "step": step_id,
                "module": module,
            }
            if transport:
                entry["transport"] = transport
            systems_touched.append(entry)

    steps_succeeded = sum(1 for s in attack_path if s["success"])
    steps_failed = len(attack_path) - steps_succeeded

    return {
        "chain_id": result.get("chain_id", ""),
        "chain_name": result.get("chain_name", ""),
        "blast_radius": {
            "data_reached": data_reached,
            "systems_touched": systems_touched,
            "trust_boundaries_crossed": trust_boundaries,
            "attack_path": attack_path,
            "overall_success": result.get("success", False),
            "steps_succeeded": steps_succeeded,
            "steps_failed": steps_failed,
            "steps_total": len(attack_path),
        },
    }


def write_blast_radius_report(
    analysis: dict[str, Any],
    output_path: Path,
    fmt: str = "json",
) -> Path:
    """Write blast radius report in JSON or HTML format.

    Args:
        analysis: The blast radius analysis dict from analyze_blast_radius.
        output_path: Path to write the report file.
        fmt: Output format — 'json' or 'html'.

    Returns:
        The path where the report was written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "html":
        doc = _generate_html(analysis)
        output_path.write_text(doc, encoding="utf-8")
    elif fmt == "json":
        output_path.write_text(
            json.dumps(analysis, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        msg = f"Unsupported format '{fmt}'. Expected 'json' or 'html'."
        raise ValueError(msg)

    return output_path


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------

_SUCCESS_COLOR = "#22c55e"
_FAILURE_COLOR = "#ef4444"


def _esc(value: Any) -> str:
    """HTML-escape a value, converting to string first."""
    return html.escape(str(value))


def _generate_html(analysis: dict[str, Any]) -> str:
    """Generate a self-contained HTML blast radius report.

    Args:
        analysis: The blast radius analysis dict.

    Returns:
        Complete HTML document string.
    """
    try:
        qai_version = version("q-ai")
    except PackageNotFoundError:
        qai_version = "dev"
    br = analysis.get("blast_radius", {})
    chain_id = analysis.get("chain_id", "Unknown")
    chain_name = analysis.get("chain_name", "") or chain_id

    overall_success = br.get("overall_success", False)
    success_label = "SUCCESS" if overall_success else "FAILED"
    success_color = _SUCCESS_COLOR if overall_success else _FAILURE_COLOR

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>Blast Radius — {_esc(chain_name)}</title>\n"
        f"<style>{_css()}</style>\n"
        "</head>\n<body>\n"
        # Header
        '<div class="header">\n'
        "<h1>Blast Radius Report</h1>\n"
        f'<div class="subtitle">{_esc(chain_name)} &mdash; {_esc(chain_id)}</div>\n'
        "</div>\n"
        # Summary bar
        '<div class="summary-bar">\n'
        f'<span class="summary-badge" style="background:{success_color};">'
        f"{_esc(success_label)}</span>\n"
        f'<span class="summary-stat">{br.get("steps_succeeded", 0)}/{br.get("steps_total", 0)}'
        f" steps succeeded</span>\n"
        f'<span class="summary-stat">{br.get("steps_failed", 0)} failed</span>\n'
        "</div>\n"
        # Trust boundaries
        f"{_trust_boundaries_section(br.get('trust_boundaries_crossed', []))}\n"
        # Attack path
        f"{_attack_path_section(br.get('attack_path', []))}\n"
        # Data reached
        f"{_data_reached_section(br.get('data_reached', []))}\n"
        # Systems touched
        f"{_systems_touched_section(br.get('systems_touched', []))}\n"
        # Footer
        '<div class="footer">\n'
        f"Generated by q-ai {_esc(qai_version)} &mdash; "
        f'<a href="{_REPO_URL}">{_REPO_URL}</a>\n'
        "</div>\n"
        "</body>\n</html>"
    )


def _trust_boundaries_section(boundaries: list[str]) -> str:
    """Generate the trust boundaries section."""
    if not boundaries:
        return (
            '<h2 class="section-title">Trust Boundaries Crossed</h2>\n'
            '<p class="empty-note">No trust boundaries crossed.</p>'
        )
    items = " → ".join(f'<span class="boundary-tag">{_esc(b)}</span>' for b in boundaries)
    return (
        '<h2 class="section-title">Trust Boundaries Crossed</h2>\n'
        f'<div class="boundary-flow">{items}</div>'
    )


def _attack_path_section(attack_path: list[dict[str, Any]]) -> str:
    """Generate the attack path section."""
    if not attack_path:
        return (
            '<h2 class="section-title">Attack Path</h2>\n'
            '<p class="empty-note">No steps executed.</p>'
        )
    rows: list[str] = []
    for i, step in enumerate(attack_path, 1):
        success = step.get("success", False)
        status_color = _SUCCESS_COLOR if success else _FAILURE_COLOR
        status_label = "SUCCESS" if success else "FAILED"
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{_esc(step.get('step', ''))}</td>"
            f"<td>{_esc(step.get('module', ''))}</td>"
            f"<td>{_esc(step.get('technique', ''))}</td>"
            f'<td style="color:{status_color};font-weight:700;">{status_label}</td>'
            "</tr>"
        )
    return (
        '<h2 class="section-title">Attack Path</h2>\n'
        '<table class="findings-table">'
        "<thead><tr>"
        "<th>#</th><th>Step</th><th>Module</th><th>Technique</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _data_reached_section(data_reached: list[dict[str, str]]) -> str:
    """Generate the data reached section."""
    if not data_reached:
        return (
            '<h2 class="section-title">Data Reached</h2>\n'
            '<p class="empty-note">No data artifacts collected.</p>'
        )
    rows: list[str] = [
        "<tr>"
        f"<td>{_esc(item.get('type', ''))}</td>"
        f"<td>{_esc(item.get('value', ''))}</td>"
        f"<td>{_esc(item.get('source_step', ''))}</td>"
        "</tr>"
        for item in data_reached
    ]
    return (
        '<h2 class="section-title">Data Reached</h2>\n'
        '<table class="findings-table">'
        "<thead><tr>"
        "<th>Type</th><th>Value</th><th>Source Step</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _systems_touched_section(systems: list[dict[str, str]]) -> str:
    """Generate the systems touched section."""
    if not systems:
        return (
            '<h2 class="section-title">Systems Touched</h2>\n'
            '<p class="empty-note">No systems recorded.</p>'
        )
    rows: list[str] = [
        "<tr>"
        f"<td>{_esc(sys.get('step', ''))}</td>"
        f"<td>{_esc(sys.get('module', ''))}</td>"
        f"<td>{_esc(sys.get('transport', 'N/A'))}</td>"
        "</tr>"
        for sys in systems
    ]
    return (
        '<h2 class="section-title">Systems Touched</h2>\n'
        '<table class="findings-table">'
        "<thead><tr>"
        "<th>Step</th><th>Module</th><th>Transport</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _css() -> str:
    """Return the inline CSS stylesheet for blast radius reports."""
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
.summary-badge{
  display:inline-block;padding:0.25rem 0.75rem;border-radius:4px;
  font-size:0.8rem;font-weight:700;color:#fff;text-transform:uppercase;
}
.summary-stat{
  color:#94a3b8;font-size:0.9rem;
}
.section-title{margin:2rem 0 1rem;color:#06b6d4;font-size:1.2rem}
.empty-note{color:#94a3b8;font-style:italic;padding:0.5rem 0}
.boundary-flow{
  display:flex;flex-wrap:wrap;align-items:center;gap:0.25rem;
  padding:1rem;background:#1e293b;border-radius:8px;
}
.boundary-tag{
  display:inline-block;padding:0.25rem 0.75rem;
  background:#334155;border-radius:4px;font-size:0.85rem;
  color:#e2e8f0;font-weight:600;
}
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
.footer{
  margin-top:3rem;padding-top:1rem;border-top:1px solid #334155;
  text-align:center;color:#64748b;font-size:0.8rem;
}
@media print{
  body{background:#fff;color:#1e293b;padding:1rem}
  .header h1{color:#0e7490}
  h1,h2,h3{color:#1e293b}
  .summary-bar,.boundary-flow{background:#f8fafc;border:1px solid #e2e8f0}
  .findings-table th{background:#f1f5f9;color:#475569}
  .findings-table td{border-bottom-color:#e2e8f0}
  .footer{color:#94a3b8}
}
@media(max-width:768px){
  body{padding:1rem}
  .findings-table{font-size:0.75rem}
  .findings-table th,.findings-table td{padding:0.5rem}
}
"""
