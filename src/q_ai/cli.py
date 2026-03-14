"""Root CLI for the q-ai platform."""

from __future__ import annotations

import typer

from q_ai import __version__
from q_ai.core.cli.config import app as config_app
from q_ai.core.cli.findings import app as findings_app
from q_ai.core.cli.runs import app as runs_app
from q_ai.core.cli.targets import app as targets_app

app = typer.Typer(
    name="qai",
    help="Offensive security platform for agentic AI infrastructure.",
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"qai {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """q-ai: Offensive security platform for agentic AI infrastructure."""


app.add_typer(runs_app)
app.add_typer(findings_app)
app.add_typer(targets_app)
app.add_typer(config_app)
