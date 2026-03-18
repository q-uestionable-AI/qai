"""CLI command for checking framework taxonomy updates."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.framework_update import (
    AtlasCheckResult,
    OwaspMcpCheckResult,
    run_checks,
)

console = Console()


def _atlas_status_text(result: AtlasCheckResult) -> str:
    """Format ATLAS status cell for the summary table."""
    if result.error:
        return f"[red]{result.error}[/red]"
    if not result.new_techniques and not result.deprecated_techniques:
        return "[green]Up to date[/green]"
    parts: list[str] = []
    if result.new_techniques:
        parts.append(f"[yellow]{len(result.new_techniques)} new[/yellow]")
    if result.deprecated_techniques:
        count = len(result.deprecated_techniques)
        parts.append(f"[red]{count} deprecated[/red]")
    return ", ".join(parts)


def _owasp_status_text(result: OwaspMcpCheckResult) -> str:
    """Format OWASP MCP status cell for the summary table."""
    if result.error and not result.needs_review:
        return f"[red]{result.error}[/red]"
    if result.needs_review:
        if result.version_changed:
            return "[yellow]Version changed — review needed[/yellow]"
        return "[yellow]Manual review recommended[/yellow]"
    return "[green]Up to date[/green]"


def _print_atlas_detail(
    result: AtlasCheckResult,
    show_full_diff: bool,
) -> None:
    """Print ATLAS technique diff detail below the summary table."""
    if result.error:
        return
    if not result.new_techniques and not result.deprecated_techniques:
        return

    console.print()
    if result.new_techniques:
        count = len(result.new_techniques)
        console.print(f"[yellow]{count} new ATLAS technique(s) not in local mappings[/yellow]")
        if show_full_diff:
            for tid in result.new_techniques:
                console.print(f"  [green]+[/green] {tid}")

    if result.deprecated_techniques:
        count = len(result.deprecated_techniques)
        console.print(f"[red]{count} local technique(s) no longer in upstream ATLAS[/red]")
        if show_full_diff:
            for tid in result.deprecated_techniques:
                console.print(f"  [red]-[/red] {tid}")

    if not show_full_diff:
        console.print("[dim]Use --atlas for full technique diff.[/dim]")


def update_frameworks_cmd(
    atlas: bool = typer.Option(
        False,
        "--atlas",
        help="Show full ATLAS technique diff.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Skip cache and fetch fresh data.",
    ),
    yaml_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Check security frameworks for upstream changes.

    Fetches the latest MITRE ATLAS release and checks the OWASP MCP
    Top 10 page, compares against local frameworks.yaml, and reports
    what has changed. Never modifies frameworks.yaml.
    """
    atlas_result, owasp_result = run_checks(
        yaml_path=yaml_path,
        use_cache=not no_cache,
    )

    table = Table(title="Framework Update Status")
    table.add_column("Framework", style="cyan")
    table.add_column("Reviewed Against", style="dim")
    table.add_column("Upstream", style="bold")
    table.add_column("Status")

    table.add_row(
        "MITRE ATLAS",
        atlas_result.local_version,
        atlas_result.upstream_version or "—",
        _atlas_status_text(atlas_result),
    )

    table.add_row(
        "OWASP MCP Top 10",
        owasp_result.local_version,
        owasp_result.upstream_version or "—",
        _owasp_status_text(owasp_result),
    )

    console.print(table)

    if atlas_result.from_cache or owasp_result.from_cache:
        console.print("[dim]Results from cache. Use --no-cache to refresh.[/dim]")

    _print_atlas_detail(atlas_result, show_full_diff=atlas)
