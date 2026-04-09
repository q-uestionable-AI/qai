"""CLI commands for managing targets."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.cli.prompt import (
    build_teaching_tip,
    is_tty,
    parse_meta_flags,
    prompt_or_fail_multiple,
)
from q_ai.core.db import create_target, get_connection, list_targets

app = typer.Typer(name="targets", help="Manage scan targets.", no_args_is_help=True)
console = Console()


@app.command("list")
def list_cmd(
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """List all targets."""
    with get_connection(db_path) as conn:
        targets = list_targets(conn)
    table = Table(title="Targets")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("URI")
    for t in targets:
        table.add_row(
            t.id[:8],
            t.type,
            t.name,
            t.uri or "",
        )
    console.print(table)


@app.command(
    "add",
    epilog=(
        "Examples:\n"
        '  qai targets add "My Server" http://localhost:3000/sse\n'
        '  qai targets add "Local MCP" http://localhost:8080 --meta transport=sse\n'
        "  qai targets add  (interactive — prompts for name and URI)\n"
        "\n"
        "Common --meta keys: transport, environment, owner, notes"
    ),
)
def add_cmd(
    name: str | None = typer.Argument(
        None,
        help="Target name (prompted interactively if omitted).",
    ),
    uri: str | None = typer.Argument(
        None,
        help="Target URI (prompted interactively if omitted).",
    ),
    type: str = typer.Option(  # noqa: A002
        "server",
        "--type",
        "-t",
        help="Target type (e.g. server, endpoint).",
    ),
    meta: list[str] | None = typer.Option(
        None,
        "--meta",
        help="Metadata as key=value (repeatable). Example: --meta transport=sse",
    ),
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Add a new target.

    NAME and URI can be provided as positional arguments or entered
    interactively when running in a terminal. Type defaults to "server".

    Args:
        name: Target name. Prompted interactively if omitted in a TTY.
        uri: Target URI. Prompted interactively if omitted in a TTY.
        type: Target type (e.g. server, endpoint). Defaults to "server".
        meta: Metadata as repeatable key=value strings.
        db_path: Database path override (hidden, for testing).

    Raises:
        typer.Exit: If required args are missing in a non-TTY context.
    """
    all_provided = name is not None and uri is not None
    name, uri = prompt_or_fail_multiple(
        [
            ("NAME", name, "Target name"),
            ("URI", uri, "Target URI"),
        ]
    )

    meta_dict = parse_meta_flags(meta)

    with get_connection(db_path) as conn:
        tid = create_target(
            conn,
            type=type,
            name=name,
            uri=uri,
            metadata=meta_dict,
        )
    console.print(f"Created target {tid[:8]}")

    if not all_provided and is_tty():
        tip = build_teaching_tip("qai targets add", [name, uri])
        console.print(f"[dim]{tip}[/dim]")
