"""Generate Report workflow executor.

Produces a cross-module Markdown report and optional evidence ZIP for a
given target and time window. Pure DB analysis -- no child runs, no
adapters, no provider.

Config shape::

    {
        "target_id": str,
        "from_date": str | None,          # ISO date "YYYY-MM-DD", inclusive start of day
        "to_date": str | None,            # ISO date "YYYY-MM-DD", inclusive end of day
        "include_evidence_pack": bool,
        "output_dir": str,                # set by route before runner.start()
    }
"""

from __future__ import annotations

import datetime
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from q_ai.core.db import get_connection, get_target, list_findings
from q_ai.core.models import RunStatus, Severity

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)

_SEVERITY_NAMES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
_SEVERITY_ORDER = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
]


def _placeholders(ids: list[str]) -> str:
    """Return a comma-separated placeholder string for SQL IN clause."""
    return ", ".join("?" for _ in ids)


def _query_runs(
    conn: Any,
    target_id: str,
    from_date: str | None,
    to_date: str | None,
) -> list[Any]:
    """Resolve parent workflow run IDs for the target, filtered by date."""
    query = (
        "SELECT id, module, status, started_at FROM runs"
        " WHERE target_id = ? AND module = 'workflow'"
    )
    params: list[str] = [target_id]
    if from_date:
        query += " AND started_at >= ?"
        params.append(f"{from_date}T00:00:00")
    if to_date:
        query += " AND started_at <= ?"
        params.append(f"{to_date}T23:59:59")
    result: list[Any] = conn.execute(query, params).fetchall()
    return result


def _query_child_run_ids(conn: Any, parent_ids: list[str]) -> list[str]:
    """Resolve child run IDs for a set of parent run IDs."""
    if not parent_ids:
        return []
    ph = _placeholders(parent_ids)
    rows = conn.execute(
        f"SELECT id FROM runs WHERE parent_run_id IN ({ph})",  # noqa: S608
        parent_ids,
    ).fetchall()
    return [r["id"] for r in rows]


def _query_ipi(conn: Any, run_ids: list[str]) -> dict[str, int]:
    """Query IPI campaign stats by run ID set."""
    ph = _placeholders(run_ids)
    payloads = conn.execute(
        f"SELECT COUNT(*) FROM ipi_payloads WHERE run_id IN ({ph})",  # noqa: S608
        run_ids,
    ).fetchone()[0]
    hits = conn.execute(
        f"SELECT COUNT(*) FROM ipi_hits WHERE run_id IN ({ph})",  # noqa: S608
        run_ids,
    ).fetchone()[0]
    high_hits = conn.execute(
        f"SELECT COUNT(*) FROM ipi_hits WHERE run_id IN ({ph})"  # noqa: S608
        " AND confidence = 'high'",
        run_ids,
    ).fetchone()[0]
    return {"payloads": payloads, "hits": hits, "high_hits": high_hits}


def _query_cxp(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Query CXP test results grouped by technique and validation_result."""
    ph = _placeholders(run_ids)
    q = (
        "SELECT technique_id, validation_result, COUNT(*) as count"  # noqa: S608
        f" FROM cxp_test_results WHERE run_id IN ({ph})"
        " GROUP BY technique_id, validation_result"
    )
    rows = conn.execute(q, run_ids).fetchall()
    return [dict(r) for r in rows]


def _query_rxp(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    """Query RXP validation stats grouped by model."""
    ph = _placeholders(run_ids)
    q = (
        "SELECT model_id, AVG(retrieval_rate) as mean_retrieval,"  # noqa: S608
        " AVG(mean_poison_rank) as mean_rank"
        f" FROM rxp_validations WHERE run_id IN ({ph})"
        " GROUP BY model_id"
    )
    rows = conn.execute(q, run_ids).fetchall()
    return [dict(r) for r in rows]


def _query_chain(conn: Any, run_ids: list[str]) -> dict[str, Any]:
    """Query chain execution stats by run ID set."""
    ph = _placeholders(run_ids)
    total = conn.execute(
        f"SELECT COUNT(*) FROM chain_executions WHERE run_id IN ({ph})",  # noqa: S608
        run_ids,
    ).fetchone()[0]
    successful = conn.execute(
        f"SELECT COUNT(*) FROM chain_executions WHERE run_id IN ({ph})"  # noqa: S608
        " AND success = 1",
        run_ids,
    ).fetchone()[0]
    boundary_rows = conn.execute(
        f"SELECT trust_boundaries FROM chain_executions WHERE run_id IN ({ph})",  # noqa: S608
        run_ids,
    ).fetchall()

    boundaries: set[str] = set()
    for row in boundary_rows:
        raw = row["trust_boundaries"] or "[]"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            boundaries.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "total": total,
        "successful": successful,
        "boundaries": sorted(boundaries),
    }


def _render_report(
    target: Any,
    runner_run_id: str,
    from_date: str | None,
    to_date: str | None,
    parent_rows: list[Any],
    findings: list[Any],
    ipi: dict[str, int] | None,
    cxp: list[dict[str, Any]] | None,
    rxp: list[dict[str, Any]] | None,
    chain: dict[str, Any] | None,
) -> str:
    """Render the full report Markdown."""
    now = datetime.datetime.now(datetime.UTC).isoformat()
    scope_from = from_date or "All time"
    scope_to = to_date or "present"

    # Finding totals
    totals: dict[str, int] = dict.fromkeys(_SEVERITY_NAMES, 0)
    for f in findings:
        sev = f.severity if isinstance(f.severity, Severity) else Severity(f.severity)
        totals[sev.name] += 1

    totals_str = " | ".join(f"{name}: {totals[name]}" for name in _SEVERITY_NAMES)

    lines: list[str] = []
    lines.append(f"# Generate Report \u2014 {target.name}")
    lines.append("")
    lines.append(f"**Generated:** {now}")
    lines.append(f"**Export Run ID:** {runner_run_id}")
    uri_display = target.uri or "\u2014"
    lines.append(f"**Target:** {target.name} | {target.type} | {uri_display}")
    lines.append(f"**Scope:** {scope_from} to {scope_to}")
    lines.append("")
    lines.append(f"**Finding Totals:** {totals_str}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Runs Overview
    lines.append("## Runs Overview")
    lines.append("")
    if parent_rows:
        lines.append("| Module | Status | Started |")
        lines.append("|--------|--------|---------|")
        for row in parent_rows:
            lines.append(f"| {row['module']} | {row['status']} | {row['started_at']} |")
    else:
        lines.append("_No data in scope._")
    lines.append("")

    # Findings
    lines.append("## Findings")
    lines.append("")
    for sev_name, sev_val in zip(_SEVERITY_NAMES, _SEVERITY_ORDER, strict=True):
        lines.append(f"### {sev_name.capitalize()}")
        lines.append("")
        sev_findings = [
            f
            for f in findings
            if (f.severity if isinstance(f.severity, Severity) else Severity(f.severity)) == sev_val
        ]
        if sev_findings:
            for f in sev_findings:
                desc = f" \u2014 {f.description}" if f.description else ""
                lines.append(f"- **{f.title}** [{f.module}]{desc}")
        else:
            lines.append("_No data in scope._")
        lines.append("")

    # IPI Campaign Summary
    lines.append("## IPI Campaign Summary")
    lines.append("")
    if ipi is not None:
        lines.append(f"Payloads generated: {ipi['payloads']}")
        lines.append(f"Callback hits: {ipi['hits']}")
        lines.append(f"High-confidence hits: {ipi['high_hits']}")
    else:
        lines.append("_No data in scope._")
    lines.append("")

    # CXP Test Summary
    lines.append("## CXP Test Summary")
    lines.append("")
    if cxp is not None and cxp:
        # Pivot by technique
        techniques: dict[str, dict[str, int]] = {}
        for row in cxp:
            tid = row["technique_id"]
            vr = row["validation_result"]
            count = row["count"]
            if tid not in techniques:
                techniques[tid] = {"hit": 0, "partial": 0, "miss": 0}
            if vr in techniques[tid]:
                techniques[tid][vr] = count
        lines.append("| Technique | Hit | Partial | Miss |")
        lines.append("|-----------|-----|---------|------|")
        for tid, counts in sorted(techniques.items()):
            lines.append(f"| {tid} | {counts['hit']} | {counts['partial']} | {counts['miss']} |")
    else:
        lines.append("_No data in scope._")
    lines.append("")

    # RXP Validation Summary
    lines.append("## RXP Validation Summary")
    lines.append("")
    if rxp is not None and rxp:
        lines.append("| Model | Mean Retrieval Rate | Mean Poison Rank |")
        lines.append("|-------|--------------------:|----------------:|")
        for row in rxp:
            mr = row["mean_retrieval"]
            mp = row["mean_rank"]
            mr_str = f"{mr:.4f}" if mr is not None else "\u2014"
            mp_str = f"{mp:.2f}" if mp is not None else "\u2014"
            lines.append(f"| {row['model_id']} | {mr_str} | {mp_str} |")
    else:
        lines.append("_No data in scope._")
    lines.append("")

    # Chain Execution Summary
    lines.append("## Chain Execution Summary")
    lines.append("")
    if chain is not None:
        lines.append(f"Executions: {chain['total']} | Successful: {chain['successful']}")
        boundary_list = ", ".join(chain["boundaries"]) if chain["boundaries"] else "none"
        lines.append(f"Trust boundaries crossed: {boundary_list}")
    else:
        lines.append("_No data in scope._")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Analyst Notes")
    lines.append("")
    lines.append("<!-- Add notes here for inclusion in final report -->")
    lines.append("")

    return "\n".join(lines)


def _build_evidence_pack(
    conn: Any,
    run_ids: list[str],
    output_dir: Path,
) -> str:
    """Copy evidence files and write manifest. Returns manifest text."""
    ph = _placeholders(run_ids)
    rows = conn.execute(
        f"SELECT * FROM evidence WHERE run_id IN ({ph}) AND storage = 'file'",  # noqa: S608
        run_ids,
    ).fetchall()

    evidence_dir = output_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    included: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total_size = 0

    qai_home = str(Path.home() / ".qai")

    for row in rows:
        row_dict = dict(row)
        evidence_id = row_dict["id"]
        run_id = row_dict["run_id"]
        file_path_str = row_dict.get("path")

        if not file_path_str:
            skipped.append(
                {
                    "path": "(no path)",
                    "reason": "skipped: no path set",
                }
            )
            continue

        file_path = Path(file_path_str).resolve()

        if not str(file_path).startswith(qai_home):
            skipped.append(
                {
                    "path": file_path_str,
                    "reason": "skipped: path outside ~/.qai/",
                }
            )
            continue

        if not file_path.exists():
            skipped.append(
                {
                    "path": file_path_str,
                    "reason": "skipped: file not found",
                }
            )
            continue

        if file_path.is_dir():
            skipped.append(
                {
                    "path": file_path_str,
                    "reason": "skipped: path is directory",
                }
            )
            continue

        # Copy to evidence/<run_id>/<evidence_id>-<filename>
        dest_dir = evidence_dir / run_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{evidence_id}-{file_path.name}"
        dest_path = dest_dir / dest_name

        try:
            shutil.copy2(str(file_path), str(dest_path))
            size = dest_path.stat().st_size
            total_size += size
            included.append(
                {
                    "rel_path": f"evidence/{run_id}/{dest_name}",
                    "size": size,
                }
            )
        except OSError:
            skipped.append(
                {
                    "path": file_path_str,
                    "reason": "skipped: file not readable",
                }
            )

    now = datetime.datetime.now(datetime.UTC).isoformat()
    total_mb = total_size / (1024 * 1024)

    manifest_lines = [
        "# Evidence Manifest",
        "",
        f"**Generated:** {now}",
        f"**Total copied:** {len(included)} files ({total_mb:.2f} MB)",
        "",
        "## Included Files",
    ]
    if included:
        for item in included:
            manifest_lines.append(f"- {item['rel_path']}  ({item['size']} bytes)")
    else:
        manifest_lines.append("- (none)")
    manifest_lines.append("")
    manifest_lines.append("## Skipped Files")
    if skipped:
        for item in skipped:
            manifest_lines.append(f"- {item['path']} \u2014 {item['reason']}")
    else:
        manifest_lines.append("- (none)")
    manifest_lines.append("")

    return "\n".join(manifest_lines)


async def generate_report(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Generate a cross-module findings report and optional evidence pack.

    Pure analysis over existing DB data -- no module adapters invoked,
    no child runs created.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict -- see module docstring for shape.
    """
    output_dir = Path(config["output_dir"])

    # 1. Load target
    with get_connection(runner._db_path) as conn:
        target = get_target(conn, config["target_id"])

    if target is None:
        await runner.emit_progress(runner.run_id, "Target not found")
        await runner.complete(RunStatus.FAILED)
        return

    from_date = config.get("from_date")
    to_date = config.get("to_date")

    # 2-4. Resolve run ID set
    with get_connection(runner._db_path) as conn:
        parent_rows = _query_runs(conn, config["target_id"], from_date, to_date)
        parent_ids = [r["id"] for r in parent_rows]
        child_ids = _query_child_run_ids(conn, parent_ids)

    all_run_ids = parent_ids + child_ids

    # 5-7. Query module data (only if we have run IDs)
    findings: list[Any] = []
    ipi_data = None
    cxp_data = None
    rxp_data = None
    chain_data = None

    if all_run_ids:
        with get_connection(runner._db_path) as conn:
            findings = list_findings(conn, run_ids=all_run_ids)
            ipi_data = _query_ipi(conn, all_run_ids)
            cxp_data = _query_cxp(conn, all_run_ids)
            rxp_data = _query_rxp(conn, all_run_ids)
            chain_data = _query_chain(conn, all_run_ids)

    # 8. Render report
    report_md = _render_report(
        target=target,
        runner_run_id=runner.run_id,
        from_date=from_date,
        to_date=to_date,
        parent_rows=parent_rows,
        findings=findings,
        ipi=ipi_data,
        cxp=cxp_data,
        rxp=rxp_data,
        chain=chain_data,
    )

    # 9. Write report
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.md").write_text(report_md, encoding="utf-8")
    except OSError:
        logger.exception("Failed to write report to %s", output_dir)
        await runner.fail()
        return

    # 10. Evidence pack
    if config.get("include_evidence_pack") and all_run_ids:
        try:
            with get_connection(runner._db_path) as conn:
                manifest_md = _build_evidence_pack(conn, all_run_ids, output_dir)
            (output_dir / "manifest.md").write_text(manifest_md, encoding="utf-8")

            # Build ZIP
            zip_path = output_dir / "report.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in output_dir.rglob("*"):
                    if file == zip_path:
                        continue
                    if file.is_file():
                        zf.write(file, file.relative_to(output_dir))
        except OSError:
            logger.exception("Failed to create evidence pack at %s", output_dir)
            await runner.fail()
            return

    # 11. Emit progress
    await runner.emit_progress(
        runner.run_id,
        f"Report written to {output_dir / 'report.md'}",
    )

    # 12. Complete
    await runner.complete(RunStatus.COMPLETED)
