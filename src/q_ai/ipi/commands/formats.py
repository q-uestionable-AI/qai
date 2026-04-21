"""``qai ipi formats`` — list supported output formats."""

from __future__ import annotations

from rich.table import Table

from q_ai.ipi.commands._shared import IMPLEMENTED_FORMATS, app, console
from q_ai.ipi.generators import get_techniques_for_format
from q_ai.ipi.models import Format


@app.command()
def formats() -> None:
    """List supported output formats.

    Displays a table of all document formats with implementation status.
    """
    table = Table(title="IPI Formats")
    table.add_column("Format", style="green")
    table.add_column("Status")
    table.add_column("Techniques")

    for fmt in Format:
        if fmt in IMPLEMENTED_FORMATS:
            status = "[green]available[/green]"
            fmt_techniques = get_techniques_for_format(fmt, include_none=True)
            tech_count = f"{len(fmt_techniques)} techniques"
        else:
            status = "[dim]planned[/dim]"
            tech_count = "-"
        table.add_row(fmt.value, status, tech_count)

    console.print(table)
