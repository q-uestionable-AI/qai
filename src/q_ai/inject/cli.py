"""CLI for the inject module — tool poisoning and prompt injection testing.

Provides subcommands for serving a malicious MCP server with configurable
payloads, running injection campaigns against AI models via the Anthropic
API, listing available payloads, and rendering campaign reports.
"""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.inject.models import InjectionTechnique
from q_ai.inject.payloads.loader import filter_templates, load_all_templates
from q_ai.inject.server import build_server

app = typer.Typer(no_args_is_help=True)
console = Console()

_VALID_TRANSPORTS = ("stdio", "streamable-http")


def _parse_technique(technique: str | None) -> InjectionTechnique | None:
    """Parse a technique string into InjectionTechnique enum.

    Args:
        technique: Technique name string, or None.

    Returns:
        Parsed InjectionTechnique, or None if input is None.

    Raises:
        typer.BadParameter: If the technique string is not a valid enum value.
    """
    if technique is None:
        return None
    try:
        return InjectionTechnique(technique)
    except ValueError:
        valid = ", ".join(t.value for t in InjectionTechnique)
        raise typer.BadParameter(
            f"Unknown technique: {technique}. Valid techniques: {valid}"
        ) from None


def _load_config_names(config_path: Path) -> list[str]:
    """Load payload name whitelist from a YAML config file.

    Args:
        config_path: Path to a YAML file containing a list of payload names.

    Returns:
        List of payload name strings.

    Raises:
        typer.BadParameter: If the file cannot be read or parsed.
    """
    import yaml

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise typer.BadParameter(f"Cannot read config file: {exc}") from exc

    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        raise typer.BadParameter("Config file must contain a YAML list of payload name strings")

    return raw


@app.command()
def serve(
    transport: str = typer.Option(..., help="Transport: 'stdio' or 'streamable-http'"),
    port: int = typer.Option(8888, help="Port for streamable-http listener"),
    payload_dir: str | None = typer.Option(None, help="Directory of payload templates to serve"),
    config: str | None = typer.Option(None, help="Payload configuration YAML file"),
) -> None:
    """Start a malicious MCP server serving configurable payloads.

    The server presents MCP tools with poisoned descriptions and/or
    returns injection payloads in tool responses. Connect any MCP
    client to test how it handles adversarial tool content.
    """
    if transport not in _VALID_TRANSPORTS:
        raise typer.BadParameter(
            f"Invalid transport: {transport}. Must be one of: {', '.join(_VALID_TRANSPORTS)}"
        )

    dir_path = Path(payload_dir) if payload_dir else None
    templates = load_all_templates(template_dir=dir_path)

    if config is not None:
        names = _load_config_names(Path(config))
        templates = [t for t in templates if t.name in names]

    if not templates:
        console.print(
            "[yellow]Warning:[/yellow] No payload templates loaded. Server will have no tools."
        )

    console.print(f"[green]Building server with {len(templates)} payload(s)...[/green]")
    server = build_server(templates)

    console.print(f"[green]Starting inject server via {transport}...[/green]")
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = "0.0.0.0"  # noqa: S104  # nosec B104
        server.settings.port = port
        server.run(transport="streamable-http")


@app.command()
def campaign(
    model: str | None = typer.Option(
        None,
        help="Anthropic model ID (e.g., claude-sonnet-4-6). Falls back to QAI_MODEL env var.",
    ),
    rounds: int = typer.Option(1, help="Number of attempts per payload"),
    output: str = typer.Option(".", help="Output directory for campaign JSON"),
    payloads: str = typer.Option("all", help="Comma-separated payload names, or 'all'"),
    technique: str | None = typer.Option(
        None,
        help="Filter by technique: 'description_poisoning', 'output_injection', "
        "'cross_tool_escalation'",
    ),
    target: str | None = typer.Option(
        None,
        help="Filter by target agent (e.g., 'claude', 'gpt')",
    ),
) -> None:
    """Run an injection campaign against an AI model.

    Systematically tests poisoned tool payloads against the target model
    via the Anthropic API, scoring each for effectiveness. Requires
    ANTHROPIC_API_KEY to be set. Results are saved as structured JSON.
    """
    import asyncio

    from q_ai.inject.campaign import run_campaign

    resolved_model = model or os.environ.get("QAI_MODEL")
    if not resolved_model:
        console.print(
            "[red]Error:[/red] No model specified. Use --model flag or set "
            "QAI_MODEL environment variable."
        )
        raise typer.Exit(code=1)

    templates = load_all_templates()
    templates = filter_templates(
        templates, technique=_parse_technique(technique), target_agent=target
    )

    # Apply payload name filter
    if payloads != "all":
        names = {n.strip() for n in payloads.split(",")}
        templates = [t for t in templates if t.name in names]

    if not templates:
        console.print("[yellow]No payloads matched the given filters.[/yellow]")
        raise typer.Exit(1)

    console.print("[bold blue]qai inject campaign[/bold blue]")
    console.print(f"  Model:    {resolved_model}")
    console.print(f"  Payloads: {len(templates)}")
    console.print(f"  Rounds:   {rounds}")
    console.print()

    async def _run() -> None:
        result = await run_campaign(
            templates=templates,
            model=resolved_model,
            rounds=rounds,
            output_dir=Path(output),
        )

        from q_ai.inject.mapper import persist_campaign

        persist_campaign(result)

        summary = result.summary()

        console.print("\n[bold]Campaign Complete[/bold]")
        console.print(f"  Total results:       {summary['total']}")
        console.print(f"  Full compliance:     {summary['full_compliance']}")
        console.print(f"  Partial compliance:  {summary['partial_compliance']}")
        console.print(f"  Refusal with leak:   {summary['refusal_with_leak']}")
        console.print(f"  Clean refusal:       {summary['clean_refusal']}")
        console.print(f"  Errors:              {summary['error']}")
        console.print(f"\n[dim]Results saved to {Path(output) / (result.id + '.json')}[/dim]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Campaign interrupted.[/yellow]")
        raise typer.Exit(130) from None


@app.command(name="list-payloads")
def list_payloads(
    technique: str | None = typer.Option(
        None,
        help="Filter by technique: 'description_poisoning', 'output_injection', "
        "'cross_tool_escalation'",
    ),
    target: str | None = typer.Option(
        None,
        help="Filter by target agent: 'claude', 'gpt', 'copilot', etc.",
    ),
) -> None:
    """List available injection payload templates."""
    templates = load_all_templates()
    templates = filter_templates(
        templates, technique=_parse_technique(technique), target_agent=target
    )

    table = Table(title="Injection Payload Templates")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Technique", style="green", no_wrap=True)
    table.add_column("Tool Name", style="magenta", no_wrap=True)
    table.add_column("OWASP IDs", style="yellow", no_wrap=True)
    table.add_column("Description", max_width=50)

    for t in templates:
        desc = t.description[:50] + "..." if len(t.description) > 50 else t.description
        table.add_row(
            t.name,
            t.technique.value,
            t.tool_name,
            ", ".join(t.owasp_ids),
            desc,
        )

    console.print(table)


@app.command()
def report(
    input_file: str = typer.Option(..., "--input", "-i", help="Path to campaign JSON file"),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: 'table' or 'json'"
    ),
) -> None:
    """Render a summary report from campaign results.

    Loads a campaign JSON file and displays a Rich table summary.
    Use --format json to output the raw campaign JSON.
    """
    import json as json_mod

    from q_ai.inject.models import Campaign

    input_path = Path(input_file)
    if not input_path.exists():
        console.print(f"[red]Input file not found:[/red] {input_path}")
        raise typer.Exit(1)

    try:
        raw = json_mod.loads(input_path.read_text(encoding="utf-8"))
    except json_mod.JSONDecodeError as exc:
        console.print(f"[red]Failed to parse campaign JSON:[/red] {exc}")
        raise typer.Exit(1) from None

    if output_format == "json":
        console.print_json(json_mod.dumps(raw, indent=2))
        return

    campaign_obj = Campaign.from_dict(raw)

    console.print("[bold blue]qai inject report[/bold blue]")
    console.print(f"  Campaign: {campaign_obj.id}")
    console.print(f"  Model:    {campaign_obj.model}")
    console.print()

    outcome_color = {
        "full_compliance": "red",
        "partial_compliance": "yellow",
        "refusal_with_leak": "bright_yellow",
        "clean_refusal": "green",
        "error": "dim",
    }

    table = Table(title="Campaign Results")
    table.add_column("Payload", style="cyan", no_wrap=True)
    table.add_column("Technique", style="green", no_wrap=True)
    table.add_column("Outcome", no_wrap=True)
    table.add_column("Evidence", max_width=60)

    for r in campaign_obj.results:
        color = outcome_color.get(r.outcome.value, "white")
        outcome_str = f"[{color}]{r.outcome.value}[/{color}]"
        evidence_text = r.evidence.replace("\n", " ").replace("\r", " ")
        evidence_preview = evidence_text[:80] + "..." if len(evidence_text) > 80 else evidence_text
        table.add_row(r.payload_name, r.technique, outcome_str, evidence_preview)

    console.print(table)

    # Summary row
    summary = campaign_obj.summary()
    console.print(f"\n[bold]Summary[/bold] ({summary['total']} results)")
    for outcome_name, count in summary.items():
        if outcome_name == "total":
            continue
        color = outcome_color.get(outcome_name, "white")
        console.print(f"  [{color}]{outcome_name}[/{color}]: {count}")
