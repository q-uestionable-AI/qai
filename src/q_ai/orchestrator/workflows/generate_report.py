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


def _render_findings_section(findings: list[dict[str, Any]]) -> str:
    """Render the Findings section grouped by severity."""
    lines = ["\n## Findings\n"]
    if not findings:
        lines.append(_EMPTY_MSG)
        return "\n".join(lines)

    current_severity = None
    for f in findings:
        sev = Severity(f["severity"])
        label = _SEVERITY_LABELS.get(sev, "INFO")
        if label != current_severity:
            current_severity = label
            lines.append(f"\n### {label}\n")
        lines.append(f"- **[{f['module']}]** {f.get('category', 'general')}: {f['title']}")
    return "\n".join(lines)


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
              ipi, cxp_results, rxp_results, chain_execs, chain_steps,
              generated_at.
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
        _render_findings_section(data["findings"]),
        _render_ipi_section(data["ipi"]),
        _render_cxp_section(data["cxp_results"]),
        _render_rxp_section(data["rxp_results"]),
        _render_chain_section(data["chain_execs"], data["chain_steps"]),
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
            all_run_ids = parent_ids + child_ids

            # --- Query all data scoped by run IDs ---
            runs = _query_runs(conn, all_run_ids)
            findings = _query_findings(conn, all_run_ids)
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
