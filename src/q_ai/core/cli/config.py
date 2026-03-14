"""CLI commands for configuration management."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from q_ai.core.config import resolve, set_credential
from q_ai.core.db import get_connection, set_setting

app = typer.Typer(name="config", help="Manage configuration.", no_args_is_help=True)
console = Console()


@app.command("get")
def get_cmd(
    key: str = typer.Argument(help="Configuration key to look up."),
    db_path: Path | None = typer.Option(None, hidden=True),
    config_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Get a configuration value using the precedence chain."""
    value, source = resolve(key, db_path=db_path, config_path=config_path)
    if value is None:
        console.print(f"{key}: [dim](not set)[/dim]")
    else:
        console.print(f"{key} = {value}  [dim](source: {source})[/dim]")


@app.command("set")
def set_cmd(
    key: str = typer.Argument(help="Setting key."),
    value: str = typer.Argument(help="Setting value."),
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Set a configuration value in the database."""
    with get_connection(db_path) as conn:
        set_setting(conn, key, value)
    console.print(f"Set {key} = {value}")


@app.command("set-credential")
def set_credential_cmd(
    provider: str = typer.Argument(help="Provider name (e.g. anthropic)."),
    api_key: str = typer.Argument(help="API key."),
    config_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Store a provider API key in the config file."""
    set_credential(provider, api_key, config_path)
    console.print(f"Credential for {provider} saved.")
