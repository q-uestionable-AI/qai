"""CLI command for checking framework taxonomy updates."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.update_frameworks import FrameworkStatus, check_frameworks

app = typer.Typer()
console = Console()

STATUS_STYLES = {
    "up-to-date": "[green]Up to date[/green]",
    "update-available": "[yellow]Update available[/yellow]",
    "error": "[red]Error[/red]",
    "check-skipped": "[dim]Skipped[/dim]",
}


@app.callback(invoke_without_command=True)
def update_frameworks(
    atlas: bool = typer.Option(
        False,
        "--atlas",
        help="Show full ATLAS technique diff detail.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Skip cache and force fresh checks.",
    ),
    yaml_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Check configured frameworks for upstream changes."""
    results = check_frameworks(yaml_path, skip_cache=no_cache)
    _print_summary_table(results)

    if atlas:
        _print_atlas_diff(results)


def _print_summary_table(results: list[FrameworkStatus]) -> None:
    """Print the summary Rich table for all framework checks.

    Args:
        results: List of framework check results.
    """
    table = Table(title="Framework Update Status")
    table.add_column("Framework", style="bold", no_wrap=True)
    table.add_column("Reviewed Against")
    table.add_column("Upstream")
    table.add_column("Status")
    table.add_column("Details")

    for r in results:
        status_text = STATUS_STYLES.get(r.status, r.status)
        table.add_row(
            r.framework,
            r.local_version,
            r.upstream_version,
            status_text,
            r.message,
        )

    console.print(table)


def _print_atlas_diff(results: list[FrameworkStatus]) -> None:
    """Print detailed ATLAS technique diff if available.

    Args:
        results: List of framework check results.
    """
    atlas_result = next((r for r in results if r.framework == "mitre_atlas"), None)
    if atlas_result is None or atlas_result.atlas_diff is None:
        return

    diff = atlas_result.atlas_diff
    if not diff.new_techniques and not diff.deprecated_techniques:
        console.print("\n[dim]No technique ID differences found.[/dim]")
        return

    console.print()
    if diff.new_techniques:
        table = Table(title="New Upstream Techniques (not yet mapped)")
        table.add_column("Technique ID", style="green")
        for tid in diff.new_techniques:
            table.add_row(tid)
        console.print(table)

    if diff.deprecated_techniques:
        table = Table(title="Deprecated Techniques (in local mappings but not upstream)")
        table.add_column("Technique ID", style="red")
        for tid in diff.deprecated_techniques:
            table.add_row(tid)
        console.print(table)
