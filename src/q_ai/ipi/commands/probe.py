"""``qai ipi probe`` — measure model susceptibility to IPI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from q_ai.core.cli.prompt import build_teaching_tip, is_tty, prompt_or_fail
from q_ai.ipi.commands._shared import app, console

if TYPE_CHECKING:
    from q_ai.ipi.probe_service import Probe, ProbeRunResult


def _display_probe_dry_run(probes: list[Probe]) -> None:
    """Display a table of probes for dry-run mode.

    Args:
        probes: List of Probe objects to display.
    """
    table = Table(title="IPI Probes (dry run)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("Description")

    for idx, p in enumerate(probes, start=1):
        table.add_row(str(idx), p.id, p.category, p.description)

    console.print(table)
    console.print(f"\n[bold]{len(probes)}[/bold] probes would be sent.")


def _display_probe_results(run_result: ProbeRunResult, run_id: str) -> None:
    """Display probe results as a Rich table with per-category breakdown.

    Args:
        run_result: ProbeRunResult with category_stats and totals.
        run_id: The database run ID for display.
    """
    severity_colors = {
        "INFO": "dim",
        "LOW": "green",
        "MEDIUM": "yellow",
        "HIGH": "red",
        "CRITICAL": "bold red",
    }

    table = Table(title="IPI Probe Results")
    table.add_column("Category", style="magenta")
    table.add_column("Probes", justify="center")
    table.add_column("Complied", justify="center")
    table.add_column("Rate", justify="center")
    table.add_column("Severity", justify="center")

    for stat in run_result.category_stats:
        sev_name = stat.severity.name
        sev_color = severity_colors.get(sev_name, "white")
        table.add_row(
            stat.category,
            str(stat.total),
            str(stat.complied),
            f"{stat.rate:.0%}",
            f"[{sev_color}]{sev_name}[/{sev_color}]",
        )

    total = run_result.total_probes
    complied = run_result.total_complied
    rate = run_result.overall_rate
    sev_name = run_result.overall_severity.name
    sev_color = severity_colors.get(sev_name, "white")
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total}[/bold]",
        f"[bold]{complied}[/bold]",
        f"[bold]{rate:.0%}[/bold]",
        f"[bold][{sev_color}]{sev_name}[/{sev_color}][/bold]",
    )

    console.print(table)
    console.print(f"\n[dim]Run ID: {run_id}[/dim]")


@app.command(
    epilog=(
        "Examples:\n"
        "  qai ipi probe http://localhost:8000/v1 --model my-model\n"
        "  qai ipi probe http://localhost:8000/v1 -m my-model --concurrency 4\n"
        "  qai ipi probe --dry-run\n"
        "  qai ipi probe  (interactive — prompts for endpoint and model)"
    ),
)
def probe(  # noqa: PLR0913 — CLI entry point, one parameter per Typer option
    endpoint_url: Annotated[
        str | None,
        typer.Argument(
            help="OpenAI-compatible API base URL (prompted interactively if omitted).",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model name for chat completions."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="Bearer token for authentication (also reads QAI_PROBE_API_KEY env var).",
        ),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", "-t", help="Target ID to associate results with."),
    ] = None,
    temperature: Annotated[
        float,
        typer.Option("--temperature", help="Sampling temperature (default 0.0)."),
    ] = 0.0,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Max parallel probe requests (default 1)."),
    ] = 1,
    probe_set: Annotated[
        Path | None,
        typer.Option("--probe-set", help="Custom YAML probe file (overrides built-in)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List probes without sending requests."),
    ] = False,
    export: Annotated[
        Path | None,
        typer.Option(
            "--export",
            "-o",
            help="Write scored-prompts JSON to this path (importable via 'qai import').",
        ),
    ] = None,
) -> None:
    """Test model susceptibility to indirect prompt injection.

    Sends a set of probe prompts to an OpenAI-compatible chat completions
    endpoint and scores responses for canary compliance. Results are
    persisted to the qai database as findings.

    ENDPOINT_URL is the API base URL (e.g. http://localhost:8000/v1);
    the service appends /chat/completions automatically.
    """
    from q_ai.ipi.probe_service import (
        export_scored_prompts,
        load_probes,
        persist_probe_run,
        run_probes,
    )

    try:
        probes = load_probes(probe_set)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading probes: {exc}[/red]")
        raise typer.Exit(1) from None

    if dry_run:
        _display_probe_dry_run(probes)
        raise typer.Exit()

    provided_directly = endpoint_url is not None
    resolved_endpoint = prompt_or_fail("ENDPOINT_URL", endpoint_url, "API base URL")

    model_provided_directly = model is not None
    resolved_model = prompt_or_fail("MODEL", model, "Model name")

    if (not provided_directly or not model_provided_directly) and is_tty():
        tip_args = [resolved_endpoint, "--model", resolved_model]
        tip = build_teaching_tip("qai ipi probe", tip_args)
        console.print(f"[dim]{tip}[/dim]")

    resolved_api_key = api_key or os.environ.get("QAI_PROBE_API_KEY")

    if concurrency < 1:
        console.print("[red]Error: --concurrency must be >= 1[/red]")
        raise typer.Exit(1)

    console.print(
        f"[bold]Running {len(probes)} probes against {resolved_model}"
        f" at {resolved_endpoint}...[/bold]"
    )
    run_result = asyncio.run(
        run_probes(
            endpoint=resolved_endpoint,
            model=resolved_model,
            probes=probes,
            api_key=resolved_api_key,
            temperature=temperature,
            concurrency=concurrency,
        )
    )

    run_id = persist_probe_run(
        run_result=run_result,
        model=resolved_model,
        endpoint=resolved_endpoint,
        target_id=target,
    )

    if export:
        actual_path = export_scored_prompts(run_result, resolved_model, resolved_endpoint, export)
        console.print(f"[green]Exported results to {actual_path}[/green]")

    _display_probe_results(run_result, run_id)
