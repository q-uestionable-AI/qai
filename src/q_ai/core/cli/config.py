"""CLI commands for configuration management."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.config import (
    delete_credential,
    get_credential,
    import_legacy_credentials,
    resolve,
    set_credential,
)
from q_ai.core.db import get_connection, set_setting

app = typer.Typer(name="config", help="Manage configuration.", no_args_is_help=True)
console = Console()

_KNOWN_PROVIDERS = ["anthropic", "openai", "groq", "mistral", "cohere", "ollama", "azure"]


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
    provider: str = typer.Argument(help="Provider name (e.g., anthropic, openai, groq, ollama)."),
) -> None:
    """Store a provider API key in the OS keyring.

    Prompts for the key securely (masked input). The key is stored
    in the OS native secret store (Windows Credential Manager,
    macOS Keychain, Linux Secret Service).
    """
    import getpass

    api_key = getpass.getpass(f"API key for {provider}: ")
    if not api_key.strip():
        console.print("[red]Error:[/red] Empty API key. Nothing saved.")
        raise typer.Exit(code=1)
    set_credential(provider, api_key.strip())
    console.print(f"Credential for [cyan]{provider}[/cyan] saved to OS keyring.")


@app.command("delete-credential")
def delete_credential_cmd(
    provider: str = typer.Argument(help="Provider name."),
) -> None:
    """Remove a provider API key from the OS keyring."""
    delete_credential(provider)
    console.print(f"Credential for [cyan]{provider}[/cyan] removed from OS keyring.")


@app.command("list-providers")
def list_providers_cmd() -> None:
    """Show which providers have credentials configured."""
    table = Table(title="Provider Credentials")
    table.add_column("Provider", style="cyan")
    table.add_column("Status")

    for provider in _KNOWN_PROVIDERS:
        try:
            cred = get_credential(provider)
        except RuntimeError:
            table.add_row(provider, "[yellow]keyring unavailable[/yellow]")
            continue
        if cred is not None:
            table.add_row(provider, "[green]configured[/green]")
        else:
            table.add_row(provider, "[dim]not configured[/dim]")

    console.print(table)


@app.command("import-legacy-credentials")
def import_legacy_credentials_cmd(
    config_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """One-time migration: move plaintext keys from config.yaml to OS keyring.

    Reads provider API keys from ~/.qai/config.yaml, writes them to the
    OS keyring, backs up the original file, and removes the keys from
    the YAML. Non-secret settings in the file are preserved.

    Idempotent -- safe to run multiple times.
    """
    results = import_legacy_credentials(config_path)

    if not results:
        console.print("[dim]No legacy credentials found in config.yaml.[/dim]")
        return

    for provider, success, message in results:
        if success:
            console.print(f"  [green]\u2713[/green] {provider}: {message}")
        else:
            console.print(f"  [red]\u2717[/red] {provider}: {message}")

    migrated = sum(1 for _, s, _ in results if s)
    console.print(f"\n{migrated}/{len(results)} credentials migrated.")
