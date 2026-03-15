"""Root CLI for the q-ai platform."""

import atexit
import threading

import typer
import uvicorn

from q_ai import __version__
from q_ai.audit.cli import app as audit_app
from q_ai.chain.cli import app as chain_app
from q_ai.core.cli.config import app as config_app
from q_ai.core.cli.findings import app as findings_app
from q_ai.core.cli.runs import app as runs_app
from q_ai.core.cli.targets import app as targets_app
from q_ai.inject.cli import app as inject_app
from q_ai.ipi.cli import app as ipi_app
from q_ai.proxy.cli import app as proxy_app
from q_ai.server.helpers import (
    delete_port_file,
    find_free_port,
    open_browser,
    write_port_file,
)

app = typer.Typer(
    name="qai",
    help="Offensive security platform for agentic AI infrastructure.",
)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"qai {__version__}")
        raise typer.Exit()


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
    """q-ai: Offensive security platform for agentic AI infrastructure."""
    if ctx.invoked_subcommand is not None:
        return

    resolved_port = port or find_free_port()
    if not 1 <= resolved_port <= 65535:
        typer.echo("Invalid port number. Must be between 1 and 65535.", err=True)
        raise typer.Exit(code=1)

    _run_server(port=resolved_port, no_browser=no_browser)


app.add_typer(runs_app)
app.add_typer(findings_app)
app.add_typer(targets_app)
app.add_typer(config_app)
app.add_typer(audit_app, name="audit", help="MCP server security scanning.")
app.add_typer(inject_app, name="inject", help="Tool poisoning and prompt injection testing.")
app.add_typer(proxy_app, name="proxy", help="MCP traffic interception and replay.")
app.add_typer(chain_app, name="chain", help="Multi-agent attack chain exploitation.")
app.add_typer(ipi_app, name="ipi", help="Indirect prompt injection testing.")
