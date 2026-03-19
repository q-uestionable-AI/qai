"""Generate Report workflow executor.

Produces a cross-module Markdown report and optional evidence ZIP for a
given target and time window. Pure DB analysis — no child runs, no
adapters, no provider.

Config shape::

    {
        "target_id": str,
        "from_date": str | None,      # ISO date, inclusive start of day
        "to_date": str | None,        # ISO date, inclusive end of day
        "include_evidence_pack": bool,
        "output_dir": str,            # Set by route before runner.start()
    }
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import shlex
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.db import get_connection, get_target
from q_ai.core.models import RunStatus, Severity

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)

_SEVERITY_LABELS = {
    Severity.CRITICAL: "CRITICAL",
    Severity.HIGH: "HIGH",
    Severity.MEDIUM: "MEDIUM",
    Severity.LOW: "LOW",
    Severity.INFO: "INFO",
}

_EMPTY_MSG = "No data in scope."

_MAX_INJECT_MATRIX_ROWS = 50


# ---------------------------------------------------------------------------
# Run scoping
# ---------------------------------------------------------------------------


def _resolve_parent_run_ids(
    conn: Any,
    target_id: str,
    from_date: str | None,
    to_date: str | None,
) -> list[str]:
    """Find parent workflow run IDs whose config contains the target_id.

    WorkflowRunner.start() calls create_run() with module='workflow' and
    stores target_id inside the config JSON, NOT in the target_id column.
    We must extract it from the JSON config.

    Args:
        conn: Active database connection.
        target_id: Target ID to match inside run config JSON.
        from_date: Optional inclusive start date (YYYY-MM-DD).
        to_date: Optional inclusive end date (YYYY-MM-DD).

    Returns:
        List of parent run IDs matching the criteria.
    """
    query = (
        "SELECT id, started_at FROM runs "
        "WHERE module = 'workflow' "
        "AND json_extract(config, '$.target_id') = ?"
    )
    params: list[object] = [target_id]

    if from_date:
        query += " AND started_at >= ?"
        params.append(f"{from_date}T00:00:00")
    if to_date:
        next_day = dt.date.fromisoformat(to_date) + dt.timedelta(days=1)
        query += " AND started_at < ?"
        params.append(f"{next_day.isoformat()}T00:00:00")

    rows = conn.execute(query, params).fetchall()
    return [row["id"] for row in rows]


def _resolve_child_run_ids(conn: Any, parent_ids: list[str]) -> list[str]:
    """Find child run IDs for the given parent runs.

    Args:
        conn: Active database connection.
        parent_ids: List of parent run IDs.

    Returns:
        List of child run IDs.
    """
    if not parent_ids:
        return []
    placeholders = ", ".join("?" for _ in parent_ids)
    rows = conn.execute(
        f"SELECT id FROM runs WHERE parent_run_id IN ({placeholders})",  # noqa: S608
        parent_ids,
    ).fetchall()
    return [r["id"] for r in rows]


def _resolve_child_runs(conn: Any, parent_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch full child run rows for the given parent runs.

    Args:
        conn: Active database connection.
        parent_ids: List of parent run IDs.

    Returns:
        List of child run dicts with id, module, config, status fields.
    """
    if not parent_ids:
        return []
    placeholders = ", ".join("?" for _ in parent_ids)
    rows = conn.execute(
        f"SELECT id, module, config, status, parent_run_id "  # noqa: S608
        f"FROM runs WHERE parent_run_id IN ({placeholders})",
        parent_ids,
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Data queries (all scoped by run-ID set)
# ---------------------------------------------------------------------------


def _query_runs(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch run rows for the given IDs."""
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT id, module, name, status, started_at, finished_at "  # noqa: S608
        f"FROM runs WHERE id IN ({placeholders}) ORDER BY started_at DESC",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_findings(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch findings scoped by run IDs."""
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM findings WHERE run_id IN ({placeholders}) "  # noqa: S608
        f"ORDER BY severity DESC, created_at DESC",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_evidence_by_finding(
    conn: Any, finding_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Fetch evidence records grouped by finding_id.

    Args:
        conn: Active database connection.
        finding_ids: List of finding IDs to query evidence for.

    Returns:
        Dict mapping finding_id to list of evidence dicts.
    """
    if not finding_ids:
        return {}
    placeholders = ", ".join("?" for _ in finding_ids)
    rows = conn.execute(
        f"SELECT id, finding_id, type, mime_type FROM evidence "  # noqa: S608
        f"WHERE finding_id IN ({placeholders})",
        finding_ids,
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        fid = r["finding_id"]
        if fid not in result:
            result[fid] = []
        result[fid].append(dict(r))
    return result


def _query_audit_scans(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch audit scan records scoped by run IDs.

    Args:
        conn: Active database connection.
        run_ids: List of run IDs to scope the query.

    Returns:
        List of audit scan dicts.
    """
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM audit_scans WHERE run_id IN ({placeholders})",  # noqa: S608
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_inject_results(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch inject results scoped by run IDs.

    Args:
        conn: Active database connection.
        run_ids: List of run IDs to scope the query.

    Returns:
        List of inject result dicts.
    """
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT technique, outcome, target_agent FROM inject_results "  # noqa: S608
        f"WHERE run_id IN ({placeholders})",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_proxy_sessions(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch proxy session records scoped by run IDs.

    Args:
        conn: Active database connection.
        run_ids: List of run IDs to scope the query.

    Returns:
        List of proxy session dicts.
    """
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM proxy_sessions WHERE run_id IN ({placeholders})",  # noqa: S608
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_ipi(conn: Any, run_ids: list[str]) -> dict[str, Any]:
    """Aggregate IPI payload and hit data."""
    if not run_ids:
        return {"payload_count": 0, "hit_count": 0, "high_confidence_hits": 0}
    placeholders = ", ".join("?" for _ in run_ids)
    payload_row = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM ipi_payloads "  # noqa: S608
        f"WHERE run_id IN ({placeholders})",
        run_ids,
    ).fetchone()
    payload_count = payload_row["cnt"] if payload_row else 0

    # Hits are linked via uuid -> payloads
    hit_row = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM ipi_hits "  # noqa: S608
        f"WHERE uuid IN (SELECT uuid FROM ipi_payloads WHERE run_id IN ({placeholders}))",
        run_ids,
    ).fetchone()
    hit_count = hit_row["cnt"] if hit_row else 0

    high_row = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM ipi_hits "  # noqa: S608
        f"WHERE confidence = 'high' "
        f"AND uuid IN (SELECT uuid FROM ipi_payloads WHERE run_id IN ({placeholders}))",
        run_ids,
    ).fetchone()
    high_confidence = high_row["cnt"] if high_row else 0

    return {
        "payload_count": payload_count,
        "hit_count": hit_count,
        "high_confidence_hits": high_confidence,
    }


def _query_cxp(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch CXP test results."""
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM cxp_test_results "  # noqa: S608
        f"WHERE run_id IN ({placeholders}) ORDER BY created_at DESC",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_rxp(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch RXP validation results."""
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM rxp_validations "  # noqa: S608
        f"WHERE run_id IN ({placeholders}) ORDER BY created_at DESC",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_chain(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch chain execution data."""
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM chain_executions "  # noqa: S608
        f"WHERE run_id IN ({placeholders}) ORDER BY created_at DESC",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_chain_steps(conn: Any, execution_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch chain step outputs for the given execution IDs."""
    if not execution_ids:
        return []
    placeholders = ", ".join("?" for _ in execution_ids)
    rows = conn.execute(
        f"SELECT * FROM chain_step_outputs "  # noqa: S608
        f"WHERE execution_id IN ({placeholders}) ORDER BY created_at",
        execution_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def _query_evidence(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch evidence records scoped by run IDs."""
    if not run_ids:
        return []
    placeholders = ", ".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT * FROM evidence "  # noqa: S608
        f"WHERE run_id IN ({placeholders}) ORDER BY created_at DESC",
        run_ids,
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _severity_totals(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Count findings by severity level."""
    totals: dict[str, int] = dict.fromkeys(_SEVERITY_LABELS.values(), 0)
    for f in findings:
        sev = Severity(f["severity"])
        label = _SEVERITY_LABELS.get(sev, "INFO")
        totals[label] += 1
    return totals


def _render_header(
    target: Any,
    run_id: str,
    from_date: str | None,
    to_date: str | None,
    severity_totals: dict[str, int],
    generated_at: str,
) -> str:
    """Render the report header section."""
    scope = "All time"
    if from_date and to_date:
        scope = f"{from_date} to {to_date}"
    elif from_date:
        scope = f"From {from_date}"
    elif to_date:
        scope = f"Through {to_date}"

    totals_line = " | ".join(f"{k}: {v}" for k, v in severity_totals.items())

    return (
        f"# Generate Report — {target.name}\n\n"
        f"**Generated:** {generated_at}\n"
        f"**Export run ID:** {run_id}\n"
        f"**Target:** {target.name} ({target.type})"
        f"{f' — {target.uri}' if target.uri else ''}\n"
        f"**Scope:** {scope}\n"
        f"**Finding totals:** {totals_line}\n"
    )


def _render_runs_section(runs: list[dict[str, Any]]) -> str:
    """Render the Runs Overview section."""
    lines = ["\n## Runs Overview\n"]
    if not runs:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    lines.append("| Run ID | Workflow / Module | Status | Started |")
    lines.append("|--------|-------------------|--------|---------|")
    for r in runs:
        if r["module"] == "workflow":  # noqa: SIM108
            display_module = r.get("name") or r["module"]
        else:
            display_module = r["module"]
        status = RunStatus(r["status"]).name
        started = (r.get("started_at") or "")[:19]
        lines.append(f"| {r['id'][:12]} | {display_module} | {status} | {started} |")
    return "\n".join(lines)


def _parse_framework_ids(raw: str | None) -> list[str]:
    """Parse framework_ids JSON string into a list of IDs.

    Args:
        raw: JSON string (array of strings) or None.

    Returns:
        List of framework ID strings, empty if unparseable.
    """
    if not raw:
        return []
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    return []


def _render_findings_section(
    findings: list[dict[str, Any]],
    evidence_by_finding: dict[str, list[dict[str, Any]]],
) -> str:
    """Render the Findings section grouped by module then severity.

    Args:
        findings: List of finding dicts from DB.
        evidence_by_finding: Dict mapping finding_id to evidence records.

    Returns:
        Markdown string for the findings section.
    """
    lines = ["\n## Findings\n"]
    if not findings:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    # Group by module -> severity
    by_module: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        mod = f.get("module", "unknown")
        if mod not in by_module:
            by_module[mod] = []
        by_module[mod].append(f)

    for module, mod_findings in by_module.items():
        lines.append(f"\n### {module}\n")
        current_severity = None
        for f in mod_findings:
            sev = Severity(f["severity"])
            label = _SEVERITY_LABELS.get(sev, "INFO")
            if label != current_severity:
                current_severity = label
                lines.append(f"\n#### {label}\n")
            lines.append(f"- **{f.get('category', 'general')}:** {f['title']}")
            _append_finding_detail(lines, f, evidence_by_finding)

    return "\n".join(lines)


def _append_finding_detail(
    lines: list[str],
    finding: dict[str, Any],
    evidence_by_finding: dict[str, list[dict[str, Any]]],
) -> None:
    """Append detail sub-bullets for a single finding.

    Args:
        lines: Accumulating list of markdown lines (mutated in place).
        finding: Single finding dict.
        evidence_by_finding: Dict mapping finding_id to evidence records.
    """
    if finding.get("description"):
        lines.append(f"  - {finding['description']}")

    framework_ids = _parse_framework_ids(finding.get("framework_ids"))
    if framework_ids:
        lines.append(f"  - Frameworks: {', '.join(framework_ids)}")

    if finding.get("source_ref"):
        lines.append(f"  - Source: {finding['source_ref']}")

    ev_list = evidence_by_finding.get(finding["id"], [])
    for ev in ev_list:
        ev_type = ev.get("type", "unknown")
        mime = ev.get("mime_type", "")
        mime_str = f" ({mime})" if mime else ""
        lines.append(f"  - Evidence: `{ev['id']}` [{ev_type}{mime_str}]")


# ---------------------------------------------------------------------------
# Per-module summary sections
# ---------------------------------------------------------------------------


def _render_audit_section(audit_scans: list[dict[str, Any]]) -> str:
    """Render the Audit Summary section with scan stats.

    Args:
        audit_scans: List of audit_scans dicts from DB.

    Returns:
        Markdown string for the audit summary section.
    """
    lines = ["\n## Audit Summary\n"]
    if not audit_scans:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    scan_count = len(audit_scans)
    transports = {s.get("transport", "unknown") for s in audit_scans}
    total_duration = sum(s.get("scan_duration_seconds") or 0.0 for s in audit_scans)

    scanner_categories: set[str] = set()
    for s in audit_scans:
        raw = s.get("scanners_run")
        if raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    scanner_categories.update(str(x) for x in parsed)

    lines.append(f"- **Scans:** {scan_count}")
    lines.append(f"- **Transports:** {', '.join(sorted(transports))}")
    if scanner_categories:
        lines.append(f"- **Scanner categories:** {', '.join(sorted(scanner_categories))}")
    lines.append(f"- **Total scan duration:** {total_duration:.1f}s")
    return "\n".join(lines)


def _render_inject_section(inject_results: list[dict[str, Any]]) -> str:
    """Render the Inject Summary section with technique x outcome matrix.

    Args:
        inject_results: List of inject result dicts with technique, outcome,
            target_agent fields.

    Returns:
        Markdown string for the inject summary section.
    """
    lines = ["\n## Inject Summary\n"]
    if not inject_results:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    # Build technique x outcome counts
    matrix: dict[str, dict[str, int]] = {}
    outcomes_seen: set[str] = set()
    for r in inject_results:
        tech = r.get("technique", "unknown")
        outcome = r.get("outcome", "unknown")
        outcomes_seen.add(outcome)
        if tech not in matrix:
            matrix[tech] = {}
        matrix[tech][outcome] = matrix[tech].get(outcome, 0) + 1

    total = len(inject_results)
    lines.append(f"**Total results:** {total}\n")

    # Cap at summary if too large
    if len(matrix) > _MAX_INJECT_MATRIX_ROWS:
        lines.append(
            f"*Matrix truncated to {_MAX_INJECT_MATRIX_ROWS} techniques "
            f"(of {len(matrix)} total).*\n"
        )

    outcome_cols = sorted(outcomes_seen)
    header = "| Technique | " + " | ".join(outcome_cols) + " |"
    separator = "|-----------|" + "|".join("---:" for _ in outcome_cols) + "|"
    lines.append(header)
    lines.append(separator)

    for i, (tech, counts) in enumerate(sorted(matrix.items())):
        if i >= _MAX_INJECT_MATRIX_ROWS:
            break
        cells = " | ".join(str(counts.get(o, 0)) for o in outcome_cols)
        lines.append(f"| {tech} | {cells} |")

    return "\n".join(lines)


def _render_proxy_section(proxy_sessions: list[dict[str, Any]]) -> str:
    """Render the Proxy Summary section with session stats.

    Args:
        proxy_sessions: List of proxy session dicts from DB.

    Returns:
        Markdown string for the proxy summary section.
    """
    lines = ["\n## Proxy Summary\n"]
    if not proxy_sessions:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    session_count = len(proxy_sessions)
    total_messages = sum(s.get("message_count") or 0 for s in proxy_sessions)
    total_duration = sum(s.get("duration_seconds") or 0.0 for s in proxy_sessions)

    lines.append(f"- **Sessions:** {session_count}")
    lines.append(f"- **Total messages intercepted:** {total_messages}")
    lines.append(f"- **Total session duration:** {total_duration:.1f}s")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Negative results
# ---------------------------------------------------------------------------


def _render_negative_results_section(
    child_runs: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    audit_scans: list[dict[str, Any]],
) -> str:
    """Render a What Was Tested section showing modules with zero findings.

    Args:
        child_runs: List of child run dicts with module field.
        findings: All findings in scope.
        audit_scans: Audit scan records for scanner count info.

    Returns:
        Markdown string for the negative results section.
    """
    lines = ["\n## What Was Tested\n"]
    if not child_runs:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    # Count findings per module
    findings_per_module: dict[str, int] = {}
    for f in findings:
        mod = f.get("module", "unknown")
        findings_per_module[mod] = findings_per_module.get(mod, 0) + 1

    # Collect modules that ran
    modules_ran: dict[str, int] = {}
    for cr in child_runs:
        mod = cr.get("module", "unknown")
        modules_ran[mod] = modules_ran.get(mod, 0) + 1

    # Count audit scanners for context
    total_scanners = 0
    for s in audit_scans:
        raw = s.get("scanners_run")
        if raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    total_scanners += len(parsed)

    for mod, _run_count in sorted(modules_ran.items()):
        fc = findings_per_module.get(mod, 0)
        extra = ""
        if mod == "audit" and total_scanners > 0:
            extra = f" ({total_scanners} scanners ran)"
        lines.append(f"- **{mod}:** {fc} findings{extra}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reproduction section
# ---------------------------------------------------------------------------

_MODULE_CLI_MAP: dict[str, dict[str, str]] = {
    "audit": {
        "_command": "qai audit scan",
        "transport": "--transport",
        "command": "--command",
        "url": "--url",
    },
    "inject": {
        "_command": "qai inject campaign",
        "model": "--model",
        "rounds": "--rounds",
    },
    "proxy": {
        "_command": "qai proxy start",
        "transport": "--transport",
        "command": "--target-command",
        "url": "--target-url",
    },
    "ipi": {
        "_command": "qai ipi generate",
        "callback_url": "--callback",
        "format": "--format",
        "payload_style": "--payload",
        "payload_type": "--payload-type",
    },
    "cxp": {
        "_command": "qai cxp generate",
        "format_id": "--format",
        "rule_ids": "--rule",
    },
    "rxp": {
        "_command": "qai rxp validate",
        "model_id": "--model",
    },
}


def _config_to_cli(module: str, config: dict[str, Any]) -> str | None:
    """Map a child run's module + config to a CLI command string.

    Args:
        module: Module name (audit, inject, proxy, etc.).
        config: Parsed config dict from the run.

    Returns:
        CLI command string, or None if no mapping exists.
    """
    mapping = _MODULE_CLI_MAP.get(module)
    if mapping is None:
        return None

    parts = [mapping["_command"]]
    for config_key, flag in mapping.items():
        if config_key.startswith("_"):
            continue
        value = config.get(config_key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            if value:
                parts.append(flag)
        elif isinstance(value, list):
            parts.extend(f"{flag} {_shell_quote(str(item))}" for item in value)
        else:
            parts.append(f"{flag} {_shell_quote(str(value))}")

    return " ".join(parts)


def _shell_quote(value: str) -> str:
    """Quote a value for safe shell display using POSIX quoting.

    Args:
        value: String value to potentially quote.

    Returns:
        Shell-safe quoted string.
    """
    return shlex.quote(value)


def _render_reproduction_section(
    child_runs: list[dict[str, Any]],
    target: Any,
) -> str:
    """Render the Reproduction section with CLI commands per child run.

    Args:
        child_runs: List of child run dicts with module and config fields.
        target: Target object with name and uri attributes.

    Returns:
        Markdown string for the reproduction section.
    """
    lines = ["\n## Reproduction\n"]
    if not child_runs:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    lines.append(
        "The following CLI commands can be used to independently reproduce each module run.\n"
    )

    for cr in child_runs:
        module = cr.get("module", "unknown")
        config_raw = cr.get("config")
        config = _parse_run_config(config_raw)

        cli_cmd = _config_to_cli(module, config)
        if cli_cmd:
            lines.append(f"**{module}** (run `{cr['id'][:12]}`):")
            lines.append(f"```\n{cli_cmd}\n```\n")

    # Raw config JSON backup
    lines.append("### Raw Config (machine-readable)\n")
    for cr in child_runs:
        module = cr.get("module", "unknown")
        config_raw = cr.get("config")
        config = _parse_run_config(config_raw)
        lines.append(f"**{module}** (`{cr['id'][:12]}`):")
        lines.append(f"```json\n{json.dumps(config, indent=2)}\n```\n")

    return "\n".join(lines)


def _parse_run_config(config_raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """Parse a run's config field from DB (may be JSON string or dict).

    Args:
        config_raw: Raw config from DB — string, dict, or None.

    Returns:
        Parsed config dict, empty dict on failure.
    """
    if config_raw is None:
        return {}
    if isinstance(config_raw, dict):
        return config_raw
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        parsed = json.loads(config_raw)
        if isinstance(parsed, dict):
            return parsed
    return {}


# ---------------------------------------------------------------------------
# Existing module summary sections
# ---------------------------------------------------------------------------


def _render_ipi_section(ipi: dict[str, Any]) -> str:
    """Render the IPI Campaign Summary section."""
    lines = ["\n## IPI Campaign Summary\n"]
    if ipi["payload_count"] == 0:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    lines.append(f"- **Payloads generated:** {ipi['payload_count']}")
    lines.append(f"- **Hits recorded:** {ipi['hit_count']}")
    lines.append(f"- **High-confidence hits:** {ipi['high_confidence_hits']}")
    return "\n".join(lines)


def _render_cxp_section(cxp_results: list[dict[str, Any]]) -> str:
    """Render the CXP Test Summary section."""
    lines = ["\n## CXP Test Summary\n"]
    if not cxp_results:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    techniques: dict[str, int] = {}
    outcomes: dict[str, int] = {"hit": 0, "partial": 0, "miss": 0}
    for r in cxp_results:
        tech = r.get("technique_id", "unknown")
        techniques[tech] = techniques.get(tech, 0) + 1
        result = r.get("validation_result", "pending").lower()
        if result in outcomes:
            outcomes[result] += 1

    lines.append("**Technique distribution:**")
    for tech, count in techniques.items():
        lines.append(f"- {tech}: {count}")
    lines.append("")
    lines.append(
        f"**Results:** {outcomes['hit']} hit | "
        f"{outcomes['partial']} partial | {outcomes['miss']} miss"
    )
    return "\n".join(lines)


def _render_rxp_section(rxp_results: list[dict[str, Any]]) -> str:
    """Render the RXP Validation Summary section."""
    lines = ["\n## RXP Validation Summary\n"]
    if not rxp_results:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    for r in rxp_results:
        model = r.get("model_id", "unknown")
        rate = r.get("retrieval_rate", 0.0)
        rank = r.get("mean_poison_rank")
        rank_str = f"{rank:.2f}" if rank is not None else "N/A"
        lines.append(f"- **{model}:** retrieval rate {rate:.1%}, mean poison rank {rank_str}")
    return "\n".join(lines)


def _render_chain_section(
    chain_execs: list[dict[str, Any]],
    chain_steps: list[dict[str, Any]],
) -> str:
    """Render the Chain Execution Summary section."""
    lines = ["\n## Chain Execution Summary\n"]
    if not chain_execs:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    total_steps = len(chain_steps)
    succeeded = sum(1 for s in chain_steps if s.get("success"))
    boundaries_crossed = 0
    for ex in chain_execs:
        tb = ex.get("trust_boundaries")
        if tb:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                boundaries_crossed += len(json.loads(tb))

    lines.append(f"- **Executions:** {len(chain_execs)}")
    lines.append(f"- **Steps succeeded:** {succeeded}/{total_steps}")
    lines.append(f"- **Trust boundaries crossed:** {boundaries_crossed}")
    return "\n".join(lines)


def _render_footer() -> str:
    """Render the report footer with Analyst Notes placeholder."""
    return (
        "\n---\n\n"
        "## Analyst Notes\n\n"
        "<!-- Add your analysis, recommendations, and context here -->\n"
    )


def _render_report(data: dict[str, Any]) -> str:
    """Assemble the full Markdown report.

    Args:
        data: Dict with keys: target, run_id, config, runs, findings,
              evidence_by_finding, audit_scans, inject_results,
              proxy_sessions, child_runs, ipi, cxp_results, rxp_results,
              chain_execs, chain_steps, generated_at.
    """
    totals = _severity_totals(data["findings"])
    config = data["config"]
    sections = [
        _render_header(
            data["target"],
            data["run_id"],
            config.get("from_date"),
            config.get("to_date"),
            totals,
            data["generated_at"],
        ),
        _render_runs_section(data["runs"]),
        _render_findings_section(data["findings"], data["evidence_by_finding"]),
        _render_audit_section(data["audit_scans"]),
        _render_inject_section(data["inject_results"]),
        _render_proxy_section(data["proxy_sessions"]),
        _render_ipi_section(data["ipi"]),
        _render_cxp_section(data["cxp_results"]),
        _render_rxp_section(data["rxp_results"]),
        _render_chain_section(data["chain_execs"], data["chain_steps"]),
        _render_negative_results_section(data["child_runs"], data["findings"], data["audit_scans"]),
        _render_reproduction_section(data["child_runs"], data["target"]),
        _render_footer(),
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Evidence pack
# ---------------------------------------------------------------------------


def _is_within_qai_dir(file_path: Path) -> bool:
    """Check if a path is within ~/.qai/ using proper containment semantics.

    Args:
        file_path: Resolved absolute path to check.

    Returns:
        True if the path is inside ~/.qai/.
    """
    qai_dir = Path.home() / ".qai"
    try:
        file_path.resolve().relative_to(qai_dir.resolve())
    except ValueError:
        return False
    else:
        return True


def _build_evidence_pack(
    evidence_rows: list[dict[str, Any]],
    output_dir: Path,
    report_content: str,
) -> None:
    """Build evidence ZIP with fixed layout.

    Args:
        evidence_rows: Evidence records from the database.
        output_dir: Directory containing report.md, where report.zip is written.
        report_content: Report markdown to include in the ZIP.
    """
    included: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    total_size = 0

    evidence_base = output_dir / "evidence"

    for ev in evidence_rows:
        ev_path_str = ev.get("path")
        ev_id = ev["id"]
        ev_run_id = ev.get("run_id", "unknown")

        if not ev_path_str:
            skipped.append({"id": ev_id, "reason": "no path recorded"})
            continue

        src_path = Path(ev_path_str)

        # Resolve to absolute for containment check
        try:
            resolved = src_path.resolve()
        except (OSError, ValueError):
            skipped.append({"id": ev_id, "path": ev_path_str, "reason": "invalid path"})
            continue

        if not _is_within_qai_dir(resolved):
            skipped.append(
                {
                    "id": ev_id,
                    "path": ev_path_str,
                    "reason": "path outside ~/.qai/",
                }
            )
            continue

        if not resolved.is_file():
            skipped.append(
                {
                    "id": ev_id,
                    "path": ev_path_str,
                    "reason": "file not found or unreadable",
                }
            )
            continue

        # Copy to evidence/<run_id>/<evidence_id>-<filename>
        dest_dir = evidence_base / ev_run_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{ev_id}-{resolved.name}"
        dest_path = dest_dir / dest_name

        try:
            shutil.copy2(str(resolved), str(dest_path))
            file_size = dest_path.stat().st_size
            total_size += file_size
            included.append(
                {
                    "id": ev_id,
                    "path": f"evidence/{ev_run_id}/{dest_name}",
                    "size": str(file_size),
                }
            )
        except OSError as e:
            skipped.append(
                {
                    "id": ev_id,
                    "path": ev_path_str,
                    "reason": f"copy failed: {e}",
                }
            )

    # Write manifest
    manifest = _render_manifest(included, skipped, total_size)
    manifest_path = output_dir / "manifest.md"
    manifest_path.write_text(manifest, encoding="utf-8")

    # Create ZIP
    zip_path = output_dir / "report.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.md", report_content)
        zf.writestr("manifest.md", manifest)
        for entry in included:
            file_on_disk = output_dir / entry["path"]
            zf.write(str(file_on_disk), entry["path"])


def _render_manifest(
    included: list[dict[str, str]],
    skipped: list[dict[str, str]],
    total_size: int,
) -> str:
    """Render the evidence manifest."""
    lines = ["# Evidence Manifest\n"]
    lines.append(f"**Total copied size:** {total_size} bytes\n")

    lines.append("## Included Files\n")
    if not included:
        lines.append("None.\n")
    else:
        lines.extend(f"- `{entry['path']}` ({entry['size']} bytes)" for entry in included)
        lines.append("")

    lines.append("## Skipped Files\n")
    if not skipped:
        lines.append("None.\n")
    else:
        for entry in skipped:
            path_str = entry.get("path", "N/A")
            lines.append(f"- ID: {entry['id']} | Path: {path_str} | Reason: {entry['reason']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


async def generate_report(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Generate a cross-module findings report and optional evidence pack.

    Pure analysis over existing DB data — no module adapters invoked,
    no child runs created.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict — see module docstring for shape.
    """
    try:
        target_id = config["target_id"]

        # --- Load target ---
        with get_connection(runner._db_path) as conn:
            target = get_target(conn, target_id)

        if target is None:
            await runner.emit_progress(runner.run_id, "Target not found")
            await runner.complete(RunStatus.FAILED)
            return

        # --- Resolve run scoping ---
        with get_connection(runner._db_path) as conn:
            parent_ids = _resolve_parent_run_ids(
                conn, target_id, config.get("from_date"), config.get("to_date")
            )
            child_ids = _resolve_child_run_ids(conn, parent_ids)
            child_runs = _resolve_child_runs(conn, parent_ids)
            all_run_ids = parent_ids + child_ids

            # --- Query all data scoped by run IDs ---
            runs = _query_runs(conn, all_run_ids)
            findings = _query_findings(conn, all_run_ids)
            finding_ids = [f["id"] for f in findings]
            evidence_by_finding = _query_evidence_by_finding(conn, finding_ids)
            audit_scans = _query_audit_scans(conn, all_run_ids)
            inject_results = _query_inject_results(conn, all_run_ids)
            proxy_sessions = _query_proxy_sessions(conn, all_run_ids)
            ipi = _query_ipi(conn, all_run_ids)
            cxp_results = _query_cxp(conn, all_run_ids)
            rxp_results = _query_rxp(conn, all_run_ids)
            chain_execs = _query_chain(conn, all_run_ids)
            exec_ids = [e["id"] for e in chain_execs]
            chain_steps = _query_chain_steps(conn, exec_ids)

        # --- Render report ---
        generated_at = dt.datetime.now(dt.UTC).isoformat()
        report_content = _render_report(
            {
                "target": target,
                "run_id": runner.run_id,
                "config": config,
                "runs": runs,
                "findings": findings,
                "evidence_by_finding": evidence_by_finding,
                "audit_scans": audit_scans,
                "inject_results": inject_results,
                "proxy_sessions": proxy_sessions,
                "child_runs": child_runs,
                "ipi": ipi,
                "cxp_results": cxp_results,
                "rxp_results": rxp_results,
                "chain_execs": chain_execs,
                "chain_steps": chain_steps,
                "generated_at": generated_at,
            }
        )

        # --- Write report ---
        output_dir = Path(config["output_dir"])
        report_path = output_dir / "report.md"
        report_path.write_text(report_content, encoding="utf-8")

        # --- Evidence pack ---
        if config.get("include_evidence_pack"):
            with get_connection(runner._db_path) as conn:
                evidence_rows = _query_evidence(conn, all_run_ids)
            _build_evidence_pack(evidence_rows, output_dir, report_content)

        await runner.emit_progress(
            runner.run_id,
            f"Report generated: {report_path}",
        )
        await runner.complete(RunStatus.COMPLETED)

    except Exception:
        logger.exception("Generate report failed for target %s", config.get("target_id"))
        await runner.emit_progress(runner.run_id, "Report generation failed")
        await runner.complete(RunStatus.FAILED)
