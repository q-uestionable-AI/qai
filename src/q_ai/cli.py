"""Root CLI for the q-ai platform."""

from __future__ import annotations

import typer

from q_ai import __version__

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
