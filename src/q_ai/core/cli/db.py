"""CLI commands for database management (backup, reset)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from q_ai.core.db import _DEFAULT_DB_PATH, get_connection
from q_ai.services.db_service import backup_database, reset_database

app = typer.Typer(name="db", help="Database management.", no_args_is_help=True)
console = Console()


@app.command("backup")
def backup_cmd(
    path: Annotated[
        Path | None,
        typer.Argument(help="Output path for the backup file."),
    ] = None,
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Create a backup of the database.

    If PATH is omitted, a timestamped backup is created under
    ``~/.qai/backups/``.

    Args:
        path: Optional output path for the backup.
        db_path: Database path override (hidden, for testing).
    """
    source = db_path or _DEFAULT_DB_PATH
    try:
        result = backup_database(source, output_path=path)
    except FileNotFoundError:
        console.print("[red]Error: database file not found.[/red]")
        raise typer.Exit(code=1) from None
    console.print(f"Backup created: {result}")


@app.command("reset")
def reset_cmd(
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Reset the database — delete all targets, runs, findings, and evidence.

    Settings are preserved. A backup is automatically created first.

    Args:
        yes: Skip the confirmation prompt.
        db_path: Database path override (hidden, for testing).
    """
    if not yes:
        if not sys.stdin.isatty():
            console.print("[red]Error: --yes required in non-interactive mode.[/red]")
            raise typer.Exit(code=1)
        typer.confirm(
            "This will delete all targets, runs, findings, and evidence. "
            "Settings will be preserved. Continue?",
            default=False,
            abort=True,
        )

    source = db_path or _DEFAULT_DB_PATH
    with get_connection(db_path) as conn:
        backup_path = reset_database(conn, source)

    if backup_path:
        console.print(f"Backup created: {backup_path}")
    console.print("Database reset complete.")
