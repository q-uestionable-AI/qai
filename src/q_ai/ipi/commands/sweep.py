"""``qai ipi sweep`` — measure per-(template, style) compliance."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from q_ai.core.cli.prompt import build_teaching_tip, is_tty, prompt_or_fail
from q_ai.ipi.commands._shared import _parse_citation_frame, app, console
from q_ai.ipi.models import DocumentTemplate, PayloadStyle, PayloadType

if TYPE_CHECKING:
    from q_ai.ipi.sweep_service import SweepCase, SweepRunResult


def _parse_templates_flag(value: str | None) -> list[DocumentTemplate]:
    """Parse a comma-separated --templates value to DocumentTemplate enums.

    Defaults to every registered template except GENERIC when value is
    None or empty.

    Args:
        value: Comma-separated template IDs (e.g. ``"whois,report"``).

    Returns:
        Ordered list of DocumentTemplate enums.

    Raises:
        typer.BadParameter: If any token does not map to a known template.
    """
    if not value or not value.strip():
        return [t for t in DocumentTemplate if t != DocumentTemplate.GENERIC]

    tokens = [tok.strip().lower() for tok in value.split(",") if tok.strip()]
    valid = {t.value: t for t in DocumentTemplate}
    unknown = [tok for tok in tokens if tok not in valid]
    if unknown:
        raise typer.BadParameter(
            f"Unknown template(s): {', '.join(unknown)}. Valid values: {', '.join(sorted(valid))}."
        )
    return [valid[tok] for tok in tokens]


def _parse_styles_flag(value: str | None) -> list[PayloadStyle]:
    """Parse a comma-separated --styles value to PayloadStyle enums.

    Defaults to ``[PayloadStyle.OBVIOUS]`` — preserves the Phase 4.4a
    single-axis baseline so existing invocations don't need flag changes.

    Args:
        value: Comma-separated style names (e.g. ``"obvious,citation"``).

    Returns:
        Ordered list of PayloadStyle enums.

    Raises:
        typer.BadParameter: If any token does not map to a known style.
    """
    if not value or not value.strip():
        return [PayloadStyle.OBVIOUS]

    tokens = [tok.strip().lower() for tok in value.split(",") if tok.strip()]
    valid = {s.value: s for s in PayloadStyle}
    unknown = [tok for tok in tokens if tok not in valid]
    if unknown:
        raise typer.BadParameter(
            f"Unknown style(s): {', '.join(unknown)}. Valid values: {', '.join(sorted(valid))}."
        )
    return [valid[tok] for tok in tokens]


def _parse_sweep_payload_type(value: str) -> PayloadType:
    """Parse --payload-type. v1 accepts CALLBACK only; reject others.

    Non-CALLBACK sweep is research work that has not been scoped; see the
    brief's Out-of-Scope note.

    Args:
        value: Payload type string (e.g. ``"callback"``).

    Returns:
        The resolved PayloadType enum.

    Raises:
        typer.BadParameter: If ``value`` is not ``"callback"``.
    """
    normalized = value.strip().lower()
    if normalized != PayloadType.CALLBACK.value:
        raise typer.BadParameter(
            f"--payload-type must be 'callback' in v1 (got {value!r})."
            " Non-callback sweep is out of scope — see the IPI sweep brief."
        )
    return PayloadType.CALLBACK


def _display_sweep_dry_run(cases: list[SweepCase], reps: int) -> None:
    """Display the (template, style) combinations that would execute.

    Args:
        cases: Combinations enumerated by :func:`build_sweep_cases`.
        reps: Repetitions per combination.
    """
    table = Table(title="IPI Sweep (dry run)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Template", style="cyan", no_wrap=True)
    table.add_column("Style", style="magenta")
    table.add_column("Payload type", style="green")
    table.add_column("Reps", justify="center")

    for idx, case in enumerate(cases, start=1):
        table.add_row(
            str(idx),
            case.template.value,
            case.style.value,
            case.payload_type.value,
            str(reps),
        )

    console.print(table)
    total = len(cases) * reps
    console.print(
        f"\n[bold]{len(cases)}[/bold] combination(s) x [bold]{reps}[/bold] rep(s)"
        f" = [bold]{total}[/bold] call(s) would be sent."
    )


def _display_sweep_results(run_result: SweepRunResult, run_id: str) -> None:
    """Display sweep results as a Rich per-(template, style) table.

    Args:
        run_result: SweepRunResult with combination_stats and totals.
        run_id: The database run ID for display.
    """
    severity_colors = {
        "INFO": "dim",
        "LOW": "green",
        "MEDIUM": "yellow",
        "HIGH": "red",
        "CRITICAL": "bold red",
    }

    table = Table(title="IPI Sweep Results")
    table.add_column("Template", style="cyan")
    table.add_column("Style", style="magenta")
    table.add_column("Reps", justify="center")
    table.add_column("Complied", justify="center")
    table.add_column("Rate", justify="center")
    table.add_column("Severity", justify="center")

    for combo in run_result.combination_stats:
        sev_name = combo.severity.name
        sev_color = severity_colors.get(sev_name, "white")
        table.add_row(
            combo.template.value,
            combo.style.value,
            str(combo.total),
            str(combo.complied),
            f"{combo.rate:.0%}",
            f"[{sev_color}]{sev_name}[/{sev_color}]",
        )

    total = run_result.total_cases
    complied = run_result.total_complied
    rate = run_result.overall_rate
    sev_name = run_result.overall_severity.name
    sev_color = severity_colors.get(sev_name, "white")
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        "",
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
        "  qai ipi sweep http://localhost:8000/v1 --model my-model\n"
        "  qai ipi sweep http://localhost:8000/v1 -m my-model --templates whois,report\n"
        "  qai ipi sweep http://localhost:8000/v1 -m my-model --styles obvious,citation\n"
        "  qai ipi sweep --dry-run\n"
        "  qai ipi sweep  (interactive — prompts for endpoint and model)"
    ),
)
def sweep(  # noqa: PLR0913 — CLI entry point, one parameter per Typer option
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
        typer.Option("--concurrency", help="Max parallel sweep requests (default 1)."),
    ] = 1,
    templates_value: Annotated[
        str | None,
        typer.Option(
            "--templates",
            help=(
                "Comma-separated template IDs (default: every registered template except GENERIC)."
            ),
        ),
    ] = None,
    styles_value: Annotated[
        str | None,
        typer.Option(
            "--styles",
            help="Comma-separated payload styles (default: obvious).",
        ),
    ] = None,
    payload_type_value: Annotated[
        str,
        typer.Option(
            "--payload-type",
            help="Attack objective. v1 accepts 'callback' only.",
        ),
    ] = "callback",
    citation_frame_value: Annotated[
        str,
        typer.Option(
            "--citation-frame",
            help=(
                "Citation-style callback rendering: 'plain' uses pre-4.5 hardcoded"
                " text (report-context baseline); 'template-aware' (default) uses"
                " per-template callback rationale. No effect on non-CITATION styles."
            ),
        ),
    ] = "template-aware",
    reps: Annotated[
        int,
        typer.Option(
            "--reps",
            help="Repetitions per (template, style) combination (default 3).",
        ),
    ] = 3,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="List (template, style) combinations without sending requests.",
        ),
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
    """Measure qai-template compliance against a target model.

    Renders each selected document-context template with a qai-generated
    payload and sends the rendered text directly to the model endpoint,
    then scores responses on whether the callback URL surfaces in the
    output. Each (template, style) combination is repeated ``--reps``
    times to produce a stable compliance rate.

    Sweep complements probe: probe measures general IPI vulnerability
    across injection categories; sweep measures which qai template
    produces highest compliance with qai's own rendered payloads — the
    Targeting stage of the Discovery -> Targeting -> Execution flow.

    ENDPOINT_URL is the API base URL (e.g. http://localhost:8000/v1);
    the service appends /chat/completions automatically.
    """
    from q_ai.ipi.sweep_service import (
        build_sweep_cases,
        export_scored_prompts,
        persist_sweep_run,
        run_sweep,
    )

    templates = _parse_templates_flag(templates_value)
    styles = _parse_styles_flag(styles_value)
    payload_type_enum = _parse_sweep_payload_type(payload_type_value)
    citation_frame_enum = _parse_citation_frame(citation_frame_value)
    cases = build_sweep_cases(templates, styles, payload_type_enum)

    if dry_run:
        _display_sweep_dry_run(cases, reps)
        raise typer.Exit()

    provided_directly = endpoint_url is not None
    resolved_endpoint = prompt_or_fail("ENDPOINT_URL", endpoint_url, "API base URL")

    model_provided_directly = model is not None
    resolved_model = prompt_or_fail("MODEL", model, "Model name")

    if (not provided_directly or not model_provided_directly) and is_tty():
        tip_args = [resolved_endpoint, "--model", resolved_model]
        tip = build_teaching_tip("qai ipi sweep", tip_args)
        console.print(f"[dim]{tip}[/dim]")

    resolved_api_key = api_key or os.environ.get("QAI_PROBE_API_KEY")

    if concurrency < 1:
        console.print("[red]Error: --concurrency must be >= 1[/red]")
        raise typer.Exit(1)
    if reps < 1:
        console.print("[red]Error: --reps must be >= 1[/red]")
        raise typer.Exit(1)

    total_calls = len(cases) * reps
    console.print(
        f"[bold]Running sweep: {len(cases)} combination(s) x {reps} rep(s)"
        f" = {total_calls} call(s) against {resolved_model} at"
        f" {resolved_endpoint}...[/bold]"
    )

    run_result = asyncio.run(
        run_sweep(
            endpoint=resolved_endpoint,
            model=resolved_model,
            cases=cases,
            reps=reps,
            temperature=temperature,
            concurrency=concurrency,
            api_key=resolved_api_key,
            citation_frame=citation_frame_enum,
        )
    )

    run_id = persist_sweep_run(
        run_result=run_result,
        model=resolved_model,
        endpoint=resolved_endpoint,
        target_id=target,
    )

    if export:
        actual_path = export_scored_prompts(run_result, resolved_model, resolved_endpoint, export)
        console.print(f"[green]Exported results to {actual_path}[/green]")

    _display_sweep_results(run_result, run_id)
