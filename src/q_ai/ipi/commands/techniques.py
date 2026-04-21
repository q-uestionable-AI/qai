"""``qai ipi techniques`` — list all hiding techniques."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from q_ai.ipi.commands._shared import _TECHNIQUE_SECTIONS, app, console


@app.command()
def techniques(
    format_name: Annotated[
        str | None,
        typer.Option(
            "--format",
            "-f",
            help="Filter by format: pdf, image, markdown, html, docx, ics, eml",
        ),
    ] = None,
) -> None:
    """List all available hiding techniques.

    Displays a table of all supported payload hiding techniques,
    organized by format and phase with descriptions.
    """
    table = Table(title="IPI Hiding Techniques")
    table.add_column("Format", style="magenta")
    table.add_column("Phase", style="cyan")
    table.add_column("Technique", style="green")
    table.add_column("Description")

    for fmt_name, phase, tech_list, desc in _TECHNIQUE_SECTIONS:
        if format_name is None or format_name.lower() == fmt_name:
            for tech in tech_list:
                table.add_row(fmt_name, phase, tech, desc.get(tech, ""))

    console.print(table)
    console.print(
        "\n[dim]Use --technique with: all, phase1, phase2, or comma-separated names[/dim]"
    )
    console.print(
        "[dim]Use --format to filter by format (pdf, image, markdown, html, docx, ics, eml)[/dim]"
    )
