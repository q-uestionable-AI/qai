"""CLI commands for managing targets."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.db import create_target, get_connection, list_targets

app = typer.Typer(name="targets", help="Manage scan targets.", no_args_is_help=True)
console = Console()


@app.command("list")
def list_cmd(
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """List all targets."""
    with get_connection(db_path) as conn:
        targets = list_targets(conn)
    table = Table(title="Targets")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("URI")
    for t in targets:
        table.add_row(
            t.id[:8],
            t.type,
            t.name,
            t.uri or "",
        )
    console.print(table)


@app.command("add")
def add_cmd(
    type: str = typer.Option(..., "--type", "-t", help="Target type (e.g. server, endpoint)."),
    name: str = typer.Option(..., "--name", "-n", help="Target name."),
    uri: str | None = typer.Option(None, "--uri", "-u", help="Target URI."),
    metadata: str | None = typer.Option(None, "--metadata", help="JSON metadata string."),
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Add a new target."""
    meta_dict = json.loads(metadata) if metadata else None
    with get_connection(db_path) as conn:
        tid = create_target(
            conn, type=type, name=name, uri=uri, metadata=meta_dict,
        )
    console.print(f"Created target {tid[:8]}")
