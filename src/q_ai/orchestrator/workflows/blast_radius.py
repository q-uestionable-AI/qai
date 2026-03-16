"""Measure Blast Radius workflow executor.

Analyzes a completed chain execution to determine reach: data accessed,
systems touched, and trust boundaries crossed. Emits findings directly
via the runner — no child runs are created.

Config shape::

    {
        "target_id": str,
        "chain_execution_id": str,   # DB id from chain_executions table
    }
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from q_ai.chain.blast_radius import analyze_blast_radius
from q_ai.core.db import get_connection
from q_ai.core.models import RunStatus, Severity

if TYPE_CHECKING:
    from q_ai.orchestrator.runner import WorkflowRunner

logger = logging.getLogger(__name__)


async def measure_blast_radius(runner: WorkflowRunner, config: dict[str, Any]) -> None:
    """Analyze blast radius from a completed chain execution.

    Pure analysis over existing DB data — no module adapters invoked,
    no child runs created. Findings are emitted directly via the runner.

    Args:
        runner: WorkflowRunner managing the parent workflow run.
        config: Configuration dict — see module docstring for shape.
    """
    try:
        # --- Load chain execution from DB ---
        with get_connection(runner._db_path) as conn:
            exec_row = conn.execute(
                "SELECT * FROM chain_executions WHERE id = ?",
                (config["chain_execution_id"],),
            ).fetchone()
            step_rows = (
                conn.execute(
                    "SELECT * FROM chain_step_outputs WHERE execution_id = ? ORDER BY created_at",
                    (exec_row["id"],),
                ).fetchall()
                if exec_row
                else []
            )

        if exec_row is None:
            await runner.emit_progress(runner.run_id, "Chain execution not found")
            await runner.complete(RunStatus.FAILED)
            return

        # --- Build result dict for analyze_blast_radius ---
        result = {
            "step_outputs": [dict(r) for r in step_rows],
            "trust_boundaries_crossed": json.loads(exec_row["trust_boundaries"] or "[]"),
        }

        analysis = analyze_blast_radius(result)

        # --- Emit findings per trust boundary crossed ---
        boundaries = result["trust_boundaries_crossed"]
        for i, boundary in enumerate(boundaries):
            await runner.emit_finding(
                finding_id=f"blast-{config['chain_execution_id'][:8]}-boundary-{i}",
                run_id=runner.run_id,
                module="chain",
                severity=int(Severity.HIGH),
                title=f"Trust boundary crossed: {boundary}",
            )

        # --- Emit summary finding ---
        n_successful = sum(1 for s in result["step_outputs"] if s.get("success"))
        systems_touched = analysis.get("systems_touched", [])
        n_systems = len(systems_touched)
        summary_severity = Severity.HIGH if n_successful > 3 else Severity.MEDIUM

        await runner.emit_finding(
            finding_id=f"blast-{config['chain_execution_id'][:8]}-summary",
            run_id=runner.run_id,
            module="chain",
            severity=int(summary_severity),
            title=f"Blast radius: {n_successful} steps reached {n_systems} systems",
        )

        await runner.emit_progress(
            runner.run_id,
            f"Analysis complete: {len(boundaries)} trust boundaries, {n_systems} systems touched",
        )
        await runner.complete(RunStatus.COMPLETED)

    except Exception:
        logger.exception(
            "Blast radius analysis failed for execution %s",
            config.get("chain_execution_id"),
        )
        await runner.emit_progress(runner.run_id, "Blast radius analysis failed")
        await runner.complete(RunStatus.FAILED)
