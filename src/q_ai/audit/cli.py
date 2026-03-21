"""CLI for the audit subcommand.

Provides commands for scanning MCP servers, enumerating capabilities,
and generating reports. Maps findings to security framework categories.
"""

from __future__ import annotations

import asyncio
import logging
import shlex

import typer
from rich.console import Console
from rich.table import Table

from q_ai.audit.orchestrator import ScanResult, run_scan
from q_ai.audit.reporting.csv_report import generate_csv_report
from q_ai.audit.reporting.html_report import generate_html_report
from q_ai.audit.reporting.json_report import generate_json_report
from q_ai.audit.reporting.ndjson_report import generate_ndjson_report
from q_ai.audit.reporting.sarif_report import generate_sarif_report
from q_ai.mcp.connection import MCPConnection
from q_ai.mcp.discovery import enumerate_server

app = typer.Typer(
    no_args_is_help=True,
    help="Scan MCP servers for security vulnerabilities",
)
console = Console()

_SEVERITY_COLORS: dict[str, str] = {
    "critical": "red",
    "high": "bright_red",
    "medium": "yellow",
    "low": "blue",
    "info": "dim",
}


def _configure_logging(verbose: bool) -> None:
    """Set up logging level based on the --verbose flag.

    Args:
        verbose: If True, set DEBUG level; otherwise INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(name)s — %(message)s")


def _resolve_output_path(format_name: str, output: str | None) -> str:
    """Determine the output file path for a scan report.

    Args:
        format_name: Report format ('json', 'sarif', or 'html').
        output: Explicit output path from the user, or None for default.

    Returns:
        Resolved output file path string.
    """
    if output is not None:
        return output
    ext_map = {"json": "json", "sarif": "sarif", "html": "html", "ndjson": "ndjson", "csv": "csv"}
    return f"results/scan.{ext_map[format_name]}"


def _print_scan_summary(result: ScanResult) -> None:
    """Print a summary of scan results to the console.

    Args:
        result: ScanResult object with tools_scanned, scanners_run,
            findings, and errors attributes.
    """
    console.print("\n[bold]Scan Complete[/bold]")
    console.print(f"  Tools scanned: {result.tools_scanned}")
    console.print(f"  Scanners run:  {', '.join(result.scanners_run)}")
    console.print(f"  Findings:      {len(result.findings)}")

    if result.findings:
        console.print("\n[bold red]Findings:[/bold red]")
        for f in result.findings:
            sev_color = _SEVERITY_COLORS.get(f.severity.value, "white")
            console.print(f"  [{sev_color}]{f.severity.value.upper()}[/{sev_color}] {f.title}")
            console.print(f"    {f.description}")
            console.print(f"    Remediation: {f.remediation}")
            console.print()

    if result.errors:
        console.print(f"\n[yellow]Errors ({len(result.errors)}):[/yellow]")
        for err in result.errors:
            console.print(f"  {err['scanner']}: {err['error']}")


def _build_connection(
    transport: str,
    command: str | None,
    url: str | None,
) -> MCPConnection:
    """Create an MCPConnection from CLI arguments.

    Args:
        transport: Transport type ('stdio', 'sse', 'streamable-http').
        command: Server command for stdio transport.
        url: Server URL for SSE or Streamable HTTP transport.

    Returns:
        Configured MCPConnection (not yet connected).

    Raises:
        typer.BadParameter: If required args are missing for the transport.
    """
    if transport == "stdio":
        if not command:
            raise typer.BadParameter("--command is required for stdio transport")
        # Split command string respecting quotes and escaped spaces
        parts = shlex.split(command)
        return MCPConnection.stdio(command=parts[0], args=parts[1:])
    if transport == "sse":
        if not url:
            raise typer.BadParameter("--url is required for SSE transport")
        return MCPConnection.sse(url=url)
    if transport == "streamable-http":
        if not url:
            raise typer.BadParameter("--url is required for streamable-http transport")
        return MCPConnection.streamable_http(url=url)
    raise typer.BadParameter(f"Unknown transport: {transport}")


@app.command()
def scan(
    transport: str = typer.Option(
        ...,
        help="Transport type: 'stdio', 'sse', or 'streamable-http'",
    ),
    command: str | None = typer.Option(
        None,
        help="Server command for stdio transport (e.g., 'python my_server.py')",
    ),
    url: str | None = typer.Option(
        None,
        help="Server URL for SSE or Streamable HTTP transport",
    ),
    checks: str | None = typer.Option(
        None,
        help="Comma-separated list of checks to run (e.g., 'injection')",
    ),
    format: str = typer.Option(  # noqa: A002
        "json",
        "--format",
        "-f",
        help="Output format: 'json', 'sarif', 'html', 'ndjson', or 'csv'",
    ),
    output: str | None = typer.Option(
        None,
        help="Output file path (default: results/scan.{json,sarif,html,ndjson,csv})",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging",
    ),
) -> None:
    """Scan an MCP server for security vulnerabilities."""
    if format not in ("json", "sarif", "html", "ndjson", "csv"):
        raise typer.BadParameter(
            f"Unknown format: {format}. Use 'json', 'sarif', 'html', 'ndjson', or 'csv'."
        )

    output = _resolve_output_path(format, output)
    _configure_logging(verbose)

    console.print("[bold blue]q-ai audit[/bold blue] — MCP Security Scanner\n")

    conn = _build_connection(transport, command, url)
    check_names = [c.strip() for c in checks.split(",")] if checks else None

    async def _do_scan() -> None:
        async with conn:
            console.print(
                f"[green]Connected[/green] to "
                f"{conn.init_result.serverInfo.name if conn.init_result.serverInfo else 'server'} "
                f"via {conn.transport_type}"
            )
            result = await run_scan(conn, check_names=check_names)

            _print_scan_summary(result)

            # Save report
            if format == "sarif":
                report_path = generate_sarif_report(result, output)
            elif format == "html":
                report_path = generate_html_report(result, output)
            elif format == "ndjson":
                report_path = generate_ndjson_report(result, output)
            elif format == "csv":
                report_path = generate_csv_report(result, output)
            else:
                report_path = generate_json_report(result, output)
            console.print(f"\n[dim]Report saved to {report_path}[/dim]")

            # Persist to database
            from q_ai.audit.mapper import persist_scan

            run_id = persist_scan(result, transport=transport)
            console.print(f"[dim]Run saved to database: {run_id}[/dim]")

    try:
        asyncio.run(_do_scan())
    except ConnectionError as exc:
        console.print(f"[red]Connection failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        console.print("\n[yellow]Scan interrupted.[/yellow]")
        raise typer.Exit(130) from None


@app.command(name="list-checks")
def list_checks(
    framework: str | None = typer.Option(
        None,
        help="Show framework IDs (e.g., 'owasp_mcp_top10', 'cwe', 'all')",
    ),
) -> None:
    """List all available scanner modules and their category mappings."""
    from q_ai.audit.scanner.registry import _REGISTRY

    console.print("[bold blue]q-ai audit[/bold blue] — Available Checks\n")

    # Full check list with categories and implementation status
    all_checks = [
        ("injection", "command_injection", "Command Injection via Tools"),
        ("auth", "auth", "Insufficient Authentication/Authorization"),
        ("token_exposure", "token_exposure", "Token Mismanagement & Secret Exposure"),
        ("permissions", "permissions", "Privilege Escalation via Tools"),
        ("tool_poisoning", "tool_poisoning", "Tool Poisoning"),
        ("prompt_injection", "prompt_injection", "Indirect Prompt Injection"),
        ("audit_telemetry", "audit_telemetry", "Insufficient Audit & Telemetry"),
        ("supply_chain", "supply_chain", "Supply Chain & Integrity"),
        ("shadow_servers", "shadow_servers", "Shadow MCP Servers"),
        ("context_sharing", "context_sharing", "Context Over-Sharing"),
    ]

    table = Table(title="Scanner Modules")
    table.add_column("Module", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Description")
    table.add_column("Status")

    if framework:
        from q_ai.core.frameworks import FrameworkResolver

        resolver = FrameworkResolver()
        table.add_column("Framework IDs", style="magenta")

    for module, category, desc in all_checks:
        status = "[green]Ready[/green]" if module in _REGISTRY else "[dim]Planned[/dim]"
        if framework:
            if framework == "all":
                fw_ids = resolver.resolve(category)
                fw_str = ", ".join(f"{k}:{v}" for k, v in fw_ids.items()) if fw_ids else ""
            else:
                fw_id = resolver.resolve_one(category, framework)
                fw_str = str(fw_id) if fw_id else ""
            table.add_row(module, category, desc, status, fw_str)
        else:
            table.add_row(module, category, desc, status)

    console.print(table)


@app.command()
def enumerate(  # noqa: A001
    transport: str = typer.Option(
        ...,
        help="Transport type: 'stdio', 'sse', or 'streamable-http'",
    ),
    command: str | None = typer.Option(
        None,
        help="Server command for stdio transport",
    ),
    url: str | None = typer.Option(
        None,
        help="Server URL for SSE or Streamable HTTP transport",
    ),
) -> None:
    """Enumerate MCP server capabilities without scanning."""
    console.print("[bold blue]q-ai audit[/bold blue] — Server Enumeration\n")

    conn = _build_connection(transport, command, url)

    async def _do_scan() -> None:
        async with conn:
            ctx = await enumerate_server(conn)

            console.print(f"[bold]Server:[/bold] {ctx.server_info.get('name', 'unknown')}")
            console.print(f"[bold]Protocol:[/bold] {ctx.server_info.get('protocolVersion', '?')}")

            if ctx.tools:
                console.print(f"\n[bold]Tools ({len(ctx.tools)}):[/bold]")
                table = Table()
                table.add_column("Name", style="cyan")
                table.add_column("Description")
                table.add_column("Parameters")
                for tool in ctx.tools:
                    params = ", ".join(tool.get("inputSchema", {}).get("properties", {}).keys())
                    table.add_row(tool["name"], tool.get("description", "")[:80], params)
                console.print(table)

            if ctx.resources:
                console.print(f"\n[bold]Resources ({len(ctx.resources)}):[/bold]")
                for r in ctx.resources:
                    console.print(f"  {r['uri']} — {r.get('description', '')}")

            if ctx.prompts:
                console.print(f"\n[bold]Prompts ({len(ctx.prompts)}):[/bold]")
                for p in ctx.prompts:
                    console.print(f"  {p['name']} — {p.get('description', '')}")

    try:
        asyncio.run(_do_scan())
    except ConnectionError as exc:
        console.print(f"[red]Connection failed:[/red] {exc}")
        raise typer.Exit(1) from exc


@app.command()
def report(
    input: str = typer.Option(  # noqa: A002
        ...,
        help="Path to saved scan results JSON file",
    ),
    format: str = typer.Option(  # noqa: A002
        "sarif",
        "--format",
        "-f",
        help="Report format: 'json', 'sarif', 'html', 'ndjson', or 'csv'",
    ),
    output: str | None = typer.Option(
        None,
        help="Output file path (defaults to input path with new extension)",
    ),
) -> None:
    """Generate a report from saved scan results.

    Loads a JSON scan result file produced by `q-ai audit scan`
    and converts it to the requested format.
    """
    import json as json_mod
    from dataclasses import dataclass, field
    from datetime import UTC, datetime
    from pathlib import Path
    from typing import Any

    from q_ai.audit.reporting.json_report import dict_to_finding

    console.print("[bold blue]q-ai audit[/bold blue] — Report Generator\n")

    if format not in ("json", "sarif", "html", "ndjson", "csv"):
        console.print(
            f"[red]Unknown format: {format}. Use 'json', 'sarif', 'html', 'ndjson', or 'csv'.[/red]"
        )
        raise typer.Exit(1)

    input_path = Path(input)
    if not input_path.exists():
        console.print(f"[red]Input file not found:[/red] {input_path}")
        raise typer.Exit(1)

    # Determine output path (avoid overwriting input file)
    if output is None:
        ext_map = {
            "json": ".json",
            "sarif": ".sarif",
            "html": ".html",
            "ndjson": ".ndjson",
            "csv": ".csv",
        }
        ext = ext_map[format]
        candidate = input_path.with_suffix(ext)
        if candidate == input_path:
            output_path = input_path.with_name(f"{input_path.stem}.report{ext}")
        else:
            output_path = candidate
    else:
        output_path = Path(output)

    # Load saved scan results
    try:
        raw = json_mod.loads(input_path.read_text())
    except json_mod.JSONDecodeError as exc:
        console.print(f"[red]Failed to parse input JSON:[/red] {input_path}")
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None
    findings = [dict_to_finding(f) for f in raw.get("findings", [])]

    # Reconstruct a minimal ScanResult-like object for the report generators
    @dataclass
    class _ReportData:
        findings: list[Any] = field(default_factory=list)
        server_info: dict[str, Any] = field(default_factory=dict)
        tools_scanned: int = 0
        scanners_run: list[str] = field(default_factory=list)
        started_at: Any = None
        finished_at: Any = None
        errors: list[dict[str, str]] = field(default_factory=list)

    scan_data = raw.get("scan", {})
    started_at_raw = scan_data.get("started_at")
    finished_at_raw = scan_data.get("finished_at")

    report_data = _ReportData(
        findings=findings,
        server_info=scan_data.get("server", {}),
        tools_scanned=scan_data.get("tools_scanned", 0),
        scanners_run=scan_data.get("scanners_run", []),
        started_at=(
            datetime.fromisoformat(started_at_raw)
            if isinstance(started_at_raw, str)
            else datetime.now(UTC)
        ),
        finished_at=(
            datetime.fromisoformat(finished_at_raw) if isinstance(finished_at_raw, str) else None
        ),
        errors=raw.get("errors", []),
    )

    if format == "sarif":
        result_path = generate_sarif_report(report_data, output_path)
    elif format == "html":
        result_path = generate_html_report(report_data, output_path)
    elif format == "ndjson":
        result_path = generate_ndjson_report(report_data, output_path)
    elif format == "csv":
        result_path = generate_csv_report(report_data, output_path)
    else:
        result_path = generate_json_report(report_data, output_path)

    console.print(f"[green]Report generated:[/green] {result_path}")
