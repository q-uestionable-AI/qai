"""CLI commands for managing runs."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.db import get_connection, list_runs
from q_ai.core.models import RunStatus

app = typer.Typer(name="runs", help="Manage scan runs.", no_args_is_help=True)
console = Console()


@app.command("list")
def list_cmd(
    module: str | None = typer.Option(None, "--module", "-m", help="Filter by module."),
    status: int | None = typer.Option(None, "--status", "-s", help="Filter by status (0-4)."),
    target: str | None = typer.Option(None, "--target", "-t", help="Filter by target ID."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results."),
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """List scan runs."""
    run_status = RunStatus(status) if status is not None else None
    with get_connection(db_path) as conn:
        runs = list_runs(conn, module=module, status=run_status, target_id=target)
    table = Table(title="Runs")
    table.add_column("ID", style="dim")
    table.add_column("Module")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Target")
    table.add_column("Started")
    for run in runs[:limit]:
        table.add_row(
            run.id[:8],
            run.module,
            run.name or "",
            run.status.name,
            run.target_id[:8] if run.target_id else "",
            str(run.started_at or ""),
        )
    console.print(table)
