"""``qai ipi reset`` — wipe all campaigns and hits."""

from __future__ import annotations

from typing import Annotated

import typer

from q_ai.ipi import db
from q_ai.ipi.commands._shared import app, console


@app.command()
def reset(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
) -> None:
    """Reset all campaigns, hits, and generated files.

    Deletes all campaign and hit records from the database and removes
    generated payload files from disk.

    Args:
        yes: Skip the confirmation prompt.
    """
    campaigns = db.get_all_campaigns()
    hits = db.get_hits()

    if not campaigns and not hits:
        console.print("[dim]Nothing to reset — database is already empty.[/dim]")
        return

    console.print(f"[bold]This will delete {len(campaigns)} campaigns and {len(hits)} hits.[/bold]")

    if not yes:
        confirm = typer.confirm("Are you sure?")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit()

    campaigns_deleted, hits_deleted, files_deleted = db.reset_db()
    console.print(
        f"[green]Done — removed {campaigns_deleted} campaigns, "
        f"{hits_deleted} hits, and {files_deleted} files.[/green]"
    )
