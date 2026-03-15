"""CLI for the chain module -- multi-agent attack chain exploitation."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.chain.loader import ChainValidationError, load_all_chains, load_chain
from q_ai.chain.models import ChainCategory, ChainDefinition, ChainResult
from q_ai.chain.tracer import trace_chain
from q_ai.chain.validator import validate_chain

app = typer.Typer(no_args_is_help=True)
console = Console()

_DEFAULT_TARGETS_PATH = Path.home() / ".qai" / "chain-targets.yaml"


@app.command()
def validate(
    chain_file: str = typer.Option(..., help="Path to chain definition to validate"),
) -> None:
    """Validate an attack chain definition without executing it.

    Checks syntax, step references, module/technique validity, and graph structure.
    """
    path = Path(chain_file)
    if not path.exists():
        typer.echo(f"Error: File not found: {chain_file}")
        raise typer.Exit(code=1)

    try:
        chain = load_chain(path)
    except ChainValidationError as exc:
        typer.echo(f"Loader error: {exc}")
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Validating chain: {chain.name} ({chain.id})\n"
        f"  {len(chain.steps)} steps, category: {chain.category.value}"
    )

    errors = validate_chain(chain)
    if errors:
        for err in errors:
            prefix = f"  Step '{err.step_id}'" if err.step_id else "  Chain"
            typer.echo(f"  [X] {prefix} [{err.field}]: {err.message}")
        raise typer.Exit(code=1)

    typer.echo("  [+] All module references valid")
    typer.echo("  [+] All technique references valid")
    typer.echo("  [+] Step graph valid (no cycles, all reachable)")
    typer.echo("  [+] Chain definition is valid")


@app.command(name="list-templates")
def list_templates(
    category: str | None = typer.Option(None, help="Filter by category"),
) -> None:
    """List available attack chain templates."""
    try:
        chains = load_all_chains()
    except ChainValidationError as exc:
        console.print(f"[red]Error:[/red] Failed to load chain templates: {exc}")
        raise typer.Exit(code=1) from exc

    if category is not None:
        try:
            cat = ChainCategory(category)
        except ValueError as exc:
            valid = ", ".join(c.value for c in ChainCategory)
            console.print(f"[red]Error:[/red] Invalid category '{category}'. Valid: {valid}")
            raise typer.Exit(code=1) from exc
        chains = [c for c in chains if c.category == cat]

    table = Table()
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Category")
    table.add_column("Steps")

    for chain in chains:
        table.add_row(chain.id, chain.name, chain.category.value, str(len(chain.steps)))

    console.print(table)


@app.command()
def run(
    chain_file: str = typer.Option(..., help="Path to attack chain definition (YAML)"),
    dry_run: bool = typer.Option(True, help="Trace path without executing destructive steps"),
    output: str | None = typer.Option(None, help="Output file for trace result JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    targets: str | None = typer.Option(None, help="Path to chain-targets.yaml config file"),
    inject_model: str | None = typer.Option(
        None,
        "--inject-model",
        help=(
            "Override inject model from targets config. "
            "Use provider/model format (e.g., anthropic/claude-sonnet-4-20250514, "
            "openai/gpt-4o). Bare model names are treated as Anthropic."
        ),
    ),
) -> None:
    """Execute an attack chain against a target architecture.

    Runs a declarative chain definition step-by-step, tracing the
    attack path and collecting evidence at each stage. Default mode
    is dry-run (simulation only).
    """
    path = Path(chain_file)
    if not path.exists():
        console.print(f"[red]Error:[/red] File not found: {chain_file}")
        raise typer.Exit(code=1)

    try:
        chain = load_chain(path)
    except ChainValidationError as exc:
        console.print(f"[red]Loader error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    errors = validate_chain(chain)
    if errors:
        console.print(f"[red]Validation failed for chain '{chain.id}':[/red]")
        for err in errors:
            prefix = f"Step '{err.step_id}'" if err.step_id else "Chain"
            console.print(f"  [red]\u2717[/red] {prefix} [{err.field}]: {err.message}")
        raise typer.Exit(code=1)

    if dry_run:
        _run_dry(chain, output)
    else:
        _run_live(chain, output, targets, inject_model)


def _run_dry(chain: ChainDefinition, output: str | None) -> None:
    """Execute dry-run trace path."""
    result = trace_chain(chain)

    console.print(f"Chain: {result.chain_name}")
    console.print("Mode: DRY RUN (no live execution)\n")
    console.print("Execution trace (success path):")

    for step in result.steps:
        boundary_str = step.trust_boundary or "none"
        chain_step = next(s for s in chain.steps if s.id == step.step_id)
        if chain_step.terminal:
            next_display = "[terminal]"
        elif chain_step.on_success:
            next_display = chain_step.on_success
        else:
            next_display = "[end]"

        console.print(
            f"  {step.order}. [cyan][{step.module}][/cyan] {step.step_id} -- {step.name}\n"
            f"     Technique: {step.technique}\n"
            f"     Trust boundary: {boundary_str} -> {next_display}\n"
        )

    if result.trust_boundaries_crossed:
        console.print("Trust boundaries crossed: " + " -> ".join(result.trust_boundaries_crossed))

    if output:
        out_path = Path(output)
        try:
            out_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]Error:[/red] unable to write trace output '{out_path}': {exc}")
            raise typer.Exit(code=1) from exc
        console.print(f"\nTrace written to {out_path}")


def _run_live(
    chain: ChainDefinition,
    output: str | None,
    targets_path: str | None,
    inject_model_override: str | None,
) -> None:
    """Execute live chain against real targets."""
    import asyncio

    from q_ai.chain.executor import execute_chain, write_chain_report
    from q_ai.chain.executor_models import TargetConfig

    config_path = Path(targets_path) if targets_path else _DEFAULT_TARGETS_PATH

    if not config_path.exists():
        if targets_path:
            console.print(f"[red]Error:[/red] Targets file not found: {config_path}")
        else:
            console.print(
                f"[red]Error:[/red] No targets file found. Provide --targets or create "
                f"{_DEFAULT_TARGETS_PATH}"
            )
        raise typer.Exit(code=1)

    try:
        target_config = TargetConfig.from_yaml(config_path)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error:[/red] Failed to load targets config: {exc}")
        raise typer.Exit(code=1) from exc

    if inject_model_override:
        target_config = target_config.with_overrides(inject_model=inject_model_override)

    console.print("[bold blue]qai chain run[/bold blue]")
    console.print(f"Chain: {chain.name}")
    console.print("Mode: [bold red]LIVE EXECUTION[/bold red]")
    console.print()

    async def _execute() -> ChainResult:
        return await execute_chain(chain, target_config)

    try:
        result = asyncio.run(_execute())
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution interrupted.[/yellow]")
        raise typer.Exit(130) from None

    _print_live_summary(chain, result)

    if output:
        out_path = Path(output)
        try:
            write_chain_report(result, out_path)
        except OSError as exc:
            console.print(f"[red]Error:[/red] unable to write report '{out_path}': {exc}")
            raise typer.Exit(code=1) from exc
        console.print(f"\n[dim]Report written to: {out_path}[/dim]")


def _print_live_summary(chain: ChainDefinition, result: ChainResult) -> None:
    """Render step-by-step live execution results to the console."""
    total_steps = len(result.step_outputs)
    step_map = {s.id: s for s in chain.steps}

    for i, step_output in enumerate(result.step_outputs, 1):
        chain_step = step_map.get(step_output.step_id)
        step_name = chain_step.name if chain_step else step_output.step_id

        status_str = "[green]SUCCESS[/green]" if step_output.success else "[red]FAILED[/red]"

        # Build status detail
        status_detail = ""
        if step_output.success and step_output.module == "audit":
            count = step_output.artifacts.get("finding_count", "0")
            status_detail = f" ({count} findings)"
        elif step_output.success and step_output.module == "inject":
            outcome = step_output.artifacts.get("best_outcome", "")
            status_detail = f" ({outcome})" if outcome else ""

        console.print(
            f"Step {i}/{total_steps}: [cyan][{step_output.module}][/cyan] "
            f"{step_output.step_id} \u2014 {step_name}"
        )
        console.print(f"  Technique: {step_output.technique}")

        # Show trust boundary if present
        if chain_step and chain_step.trust_boundary:
            console.print(f"  Trust boundary: {chain_step.trust_boundary}")

        # Show templated inputs (references to prior artifacts) if any
        if chain_step and chain_step.inputs:
            for key, val in chain_step.inputs.items():
                if str(val).startswith("$"):
                    console.print(f"  Input: {key}={val}")

        console.print(f"  Status: {status_str}{status_detail}")

        if step_output.error:
            console.print(f"  Error: [red]{step_output.error}[/red]")

        # Show artifacts
        if step_output.artifacts:
            artifact_parts = ", ".join(f"{k}={v}" for k, v in step_output.artifacts.items())
            console.print(f"  [dim]Artifacts: {artifact_parts}[/dim]")

        # Show duration
        if step_output.started_at and step_output.finished_at:
            duration = (step_output.finished_at - step_output.started_at).total_seconds()
            console.print(f"  [dim]Duration: {duration:.1f}s[/dim]")

        console.print()

    # Summary
    if result.trust_boundaries_crossed:
        console.print(
            "Trust boundaries crossed: " + " \u2192 ".join(result.trust_boundaries_crossed)
        )

    succeeded = sum(1 for s in result.step_outputs if s.success)
    success_str = "[green]SUCCESS[/green]" if result.success else "[red]FAILED[/red]"
    console.print(f"Chain result: {success_str} ({succeeded}/{total_steps} steps completed)")

    if result.started_at and result.finished_at:
        total_duration = (result.finished_at - result.started_at).total_seconds()
        console.print(f"Total duration: {total_duration:.1f}s")


@app.command(name="blast-radius")
def blast_radius(
    results: str = typer.Option(..., help="Path to chain execution result JSON file"),
    format: str = typer.Option("json", help="Output format: 'json' or 'html'"),  # noqa: A002
    output: str | None = typer.Option(None, help="Output file path"),
) -> None:
    """Analyze blast radius from a completed chain execution.

    Reads a chain execution report JSON and produces an attack path
    analysis answering: what did the attacker reach?
    """
    from q_ai.chain.blast_radius import analyze_blast_radius, write_blast_radius_report

    results_path = Path(results)
    if not results_path.exists():
        console.print(f"[red]Error:[/red] File not found: {results}")
        raise typer.Exit(code=1)

    if format not in ("json", "html"):
        console.print(f"[red]Error:[/red] Invalid format '{format}'. Use 'json' or 'html'.")
        raise typer.Exit(code=1)

    try:
        raw = json.loads(results_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Error:[/red] Failed to read results file: {exc}")
        raise typer.Exit(code=1) from exc

    analysis = analyze_blast_radius(raw)

    if output:
        out_path = Path(output)
        try:
            write_blast_radius_report(analysis, out_path, fmt=format)
        except OSError as exc:
            console.print(f"[red]Error:[/red] Failed to write report: {exc}")
            raise typer.Exit(code=1) from exc
        console.print(f"Blast radius report written to {out_path}")
    else:
        # Print to stdout
        if format == "html":
            from q_ai.chain.blast_radius import _generate_html

            typer.echo(_generate_html(analysis))
        else:
            typer.echo(json.dumps(analysis, indent=2, default=str))


@app.command()
def detect(
    results: str = typer.Option(..., help="Path to chain execution result JSON file"),
    format: str = typer.Option("sigma", help="Rule format: 'sigma' or 'wazuh'"),  # noqa: A002
    output: str | None = typer.Option(None, help="Output file or directory for detection rules"),
) -> None:
    """Generate detection rules from observed attack patterns.

    Reads a chain execution report and produces Sigma or Wazuh rules
    that would detect the observed attack patterns in a monitored
    environment.
    """
    from q_ai.chain.detection import generate_detection_rules, write_detection_rules

    results_path = Path(results)
    if not results_path.exists():
        console.print(f"[red]Error:[/red] File not found: {results}")
        raise typer.Exit(code=1)

    if format not in ("sigma", "wazuh"):
        console.print(f"[red]Error:[/red] Invalid format '{format}'. Use 'sigma' or 'wazuh'.")
        raise typer.Exit(code=1)

    try:
        raw = json.loads(results_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Error:[/red] Failed to read results file: {exc}")
        raise typer.Exit(code=1) from exc

    rules = generate_detection_rules(raw, format=format)

    if not rules:
        console.print("[yellow]No successful steps found — no detection rules generated.[/yellow]")
        raise typer.Exit(code=0)

    if output:
        out_path = Path(output)
        try:
            write_detection_rules(rules, out_path, format=format)
        except OSError as exc:
            console.print(f"[red]Error:[/red] Failed to write rules: {exc}")
            raise typer.Exit(code=1) from exc
        console.print(f"Detection rules written to {out_path}")
    else:
        # Print to stdout
        separator = "\n---\n" if format == "sigma" else "\n\n"
        typer.echo(separator.join(rules))
