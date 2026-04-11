"""Root CLI for the q-ai platform."""

import atexit
import threading

import typer
import uvicorn
from rich.console import Console

from q_ai import __version__
from q_ai.assist.cli import app as assist_app
from q_ai.audit.cli import app as audit_app
from q_ai.chain.cli import app as chain_app
from q_ai.core.cli.config import app as config_app
from q_ai.core.cli.db import app as db_app
from q_ai.core.cli.findings import app as findings_app
from q_ai.core.cli.runs import app as runs_app
from q_ai.core.cli.targets import app as targets_app
from q_ai.core.cli.update_frameworks import app as update_frameworks_app
from q_ai.cxp.cli import app as cxp_app
from q_ai.imports.cli import import_cmd
from q_ai.inject.cli import app as inject_app
from q_ai.ipi.cli import app as ipi_app
from q_ai.proxy.cli import app as proxy_app
from q_ai.rxp.cli import app as rxp_app
from q_ai.server.helpers import (
    delete_port_file,
    find_free_port,
    open_browser,
    write_port_file,
)

app = typer.Typer(
    name="qai",
    help="Security testing for agentic AI.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console()

# ---------------------------------------------------------------------------
# Help screen content
# ---------------------------------------------------------------------------

_QUICK_START = """\
[bold]Quick Start[/bold]
  qai audit scan http://localhost:3000/sse   Scan an MCP server
  qai targets add "My Server" http://...     Register a target
  qai ui                                     Launch the web UI
"""

_HINT_NO_TARGETS = (
    "[yellow]No targets found yet[/yellow] — run [bold]qai targets add[/bold] to get started."
)
_HINT_WEB_UI = "Prefer a browser? Run [bold]qai ui[/bold]"


def _print_help_screen() -> None:
    """Print the grouped help screen with quick-start examples."""
    console.print(f"\n[bold]qai[/bold] v{__version__} — Security testing for agentic AI.\n")
    console.print(_QUICK_START)

    # Contextual hint: check if any targets exist
    try:
        from pathlib import Path

        from q_ai.core.db import _DEFAULT_DB_PATH, get_connection, list_targets

        if not Path(_DEFAULT_DB_PATH).exists():
            console.print(f"  {_HINT_NO_TARGETS}\n")
        else:
            with get_connection() as conn:
                targets = list_targets(conn)
            if not targets:
                console.print(f"  {_HINT_NO_TARGETS}\n")
    except Exception:  # noqa: S110
        pass  # DB may not exist yet on first run

    console.print(f"  {_HINT_WEB_UI}\n")
    console.print(
        "[dim]Run qai --help for full command list, or qai <command> --help for details.[/dim]"
    )


# ---------------------------------------------------------------------------
# Version callback
# ---------------------------------------------------------------------------


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"qai {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Server launcher (used by `qai ui`)
# ---------------------------------------------------------------------------


def _run_server(port: int, no_browser: bool = False) -> None:
    """Start the FastAPI server and open the browser.

    Args:
        port: Port number to bind to.
        no_browser: If True, skip opening the browser.
    """
    from q_ai.server.app import create_app

    app_instance = create_app()

    write_port_file(port)
    atexit.register(delete_port_file)

    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Starting q-ai server at {url}")
    typer.echo("Press Ctrl+C to stop.")

    if not no_browser:
        # Open browser after a short delay to let uvicorn start
        timer = threading.Timer(1.0, open_browser, args=[url])
        timer.daemon = True
        timer.start()

    try:
        uvicorn.run(app_instance, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        delete_port_file()


# ---------------------------------------------------------------------------
# Root callback: bare `qai` prints help, subcommands pass through
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Root callback — prints help screen when invoked without a subcommand.

    Args:
        ctx: Typer context for subcommand detection.
        version: If True, print version and exit (eager option).
    """
    if ctx.invoked_subcommand is not None:
        return

    _print_help_screen()


# ---------------------------------------------------------------------------
# `qai ui` command
# ---------------------------------------------------------------------------


@app.command(
    "ui",
    rich_help_panel="Start",
    epilog=(
        "Examples:\n"
        "  qai ui                  Launch web UI on auto-selected port\n"
        "  qai ui --port 9000      Launch on port 9000\n"
        "  qai ui --no-browser     Start server without opening browser"
    ),
)
def ui_cmd(
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to run the web server on. Default: auto-select.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Start the server without opening a browser.",
    ),
) -> None:
    """Launch the q-ai web UI in your browser.

    Starts the FastAPI backend on the specified port (or auto-selects one)
    and opens the default browser.

    Args:
        port: Port to bind the server to. Auto-selected if omitted.
        no_browser: If True, start the server without opening a browser.

    Raises:
        typer.Exit: If the port number is invalid.
    """
    resolved_port = port or find_free_port()
    if not 1 <= resolved_port <= 65535:
        typer.echo("Invalid port number. Must be between 1 and 65535.", err=True)
        raise typer.Exit(code=1)

    _run_server(port=resolved_port, no_browser=no_browser)


# ---------------------------------------------------------------------------
# Register subcommands with grouped help panels
# ---------------------------------------------------------------------------

# Start
app.add_typer(targets_app, rich_help_panel="Start")

# Modules
app.add_typer(
    audit_app, name="audit", help="MCP server security scanning.", rich_help_panel="Modules"
)
app.add_typer(
    inject_app,
    name="inject",
    help="Tool poisoning and prompt injection testing.",
    rich_help_panel="Modules",
)
app.add_typer(
    proxy_app, name="proxy", help="MCP traffic interception and replay.", rich_help_panel="Modules"
)
app.add_typer(
    chain_app,
    name="chain",
    help="Multi-agent attack chain exploitation.",
    rich_help_panel="Modules",
)
app.add_typer(
    ipi_app, name="ipi", help="Indirect prompt injection testing.", rich_help_panel="Modules"
)
app.add_typer(
    cxp_app,
    name="cxp",
    help="Context file poisoning for coding assistants.",
    rich_help_panel="Modules",
)
app.add_typer(
    rxp_app, name="rxp", help="RAG retrieval poisoning validation.", rich_help_panel="Modules"
)

# Manage
app.add_typer(runs_app, rich_help_panel="Manage")
app.add_typer(findings_app, rich_help_panel="Manage")
app.command("import", help="Import findings from external tools.", rich_help_panel="Manage")(
    import_cmd
)
app.add_typer(config_app, rich_help_panel="Manage")
app.add_typer(db_app, rich_help_panel="Manage")
app.add_typer(
    assist_app, name="assist", help="AI assistant — guidance and results.", rich_help_panel="Manage"
)

# Utilities
app.add_typer(
    update_frameworks_app,
    name="update-frameworks",
    help="Check frameworks for upstream changes.",
    rich_help_panel="Utilities",
)
