"""TTY-aware interactive prompt utilities for CLI commands.

Provides helpers for prompting users when required arguments are missing,
inferring transport type from target strings, and printing teaching tips.
"""

import sys

import click
import typer
from rich.console import Console

console = Console(stderr=True)

# Transport inference constants
_SSE_PATH_SUFFIX = "/sse"
_URL_SCHEMES = ("http://", "https://")
_VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")


def is_tty() -> bool:
    """Check if stdin is attached to a TTY.

    Returns:
        True if stdin is a terminal, False otherwise.
    """
    return sys.stdin.isatty()


def prompt_or_fail(
    param_name: str,
    value: str | None,
    prompt_text: str,
) -> str:
    """Return value if provided, prompt if TTY, or fail with clear error.

    Args:
        param_name: Parameter name for error messages (e.g. "NAME").
        value: Current value (may be None).
        prompt_text: Text to display when prompting interactively.

    Returns:
        The resolved string value.

    Raises:
        typer.Exit: If no TTY and value is missing.
    """
    if value is not None:
        return value

    if is_tty():
        result: str = typer.prompt(prompt_text)
        return result

    console.print(f"[red]Error: missing required argument {param_name}[/red]")
    console.print(f"[dim]Provide {param_name} as a positional argument or run in a terminal.[/dim]")
    raise typer.Exit(code=1)


def prompt_or_fail_multiple(
    params: list[tuple[str, str | None, str]],
) -> list[str]:
    """Resolve multiple parameters, prompting or failing as needed.

    Args:
        params: List of (param_name, value, prompt_text) tuples.

    Returns:
        List of resolved string values in the same order.

    Raises:
        typer.Exit: If no TTY and any values are missing.
    """
    if not is_tty():
        missing = [name for name, val, _ in params if val is None]
        if missing:
            console.print(f"[red]Error: missing required argument(s): {', '.join(missing)}[/red]")
            console.print("[dim]Provide arguments positionally or run in a terminal.[/dim]")
            raise typer.Exit(code=1)

    return [prompt_or_fail(name, val, prompt) for name, val, prompt in params]


def build_teaching_tip(base_command: str, args: list[str]) -> str:
    """Build a teaching tip showing the equivalent non-interactive command.

    Args:
        base_command: The command prefix (e.g. "qai targets add").
        args: Positional argument values to quote and append.

    Returns:
        Formatted tip string.
    """
    quoted = []
    for arg in args:
        if " " in arg:
            quoted.append(f'"{arg}"')
        else:
            quoted.append(arg)
    return f"Tip: next time, run: {base_command} {' '.join(quoted)}"


def infer_transport(target: str) -> tuple[str, bool]:
    """Infer MCP transport type from a target string.

    Args:
        target: A URL or command string.

    Returns:
        Tuple of (transport_type, high_confidence).
        transport_type is one of "stdio", "sse", "streamable-http".
        high_confidence is True when inference is reliable.
    """
    target_stripped = target.strip()

    # URL-like targets
    if any(target_stripped.startswith(scheme) for scheme in _URL_SCHEMES):
        # Check for /sse path suffix
        # Strip query string and fragment for path check
        path_part = target_stripped.split("?")[0].split("#")[0]
        if path_part.rstrip("/").endswith(_SSE_PATH_SUFFIX.rstrip("/")):
            return ("sse", True)
        # URL but no clear signal — low confidence
        return ("streamable-http", False)

    # Command-like targets (no scheme, has spaces or looks like a file path)
    return ("stdio", True)


def prompt_transport(target: str) -> str:
    """Infer transport or prompt user when confidence is low.

    Args:
        target: A URL or command string.

    Returns:
        Resolved transport type string.

    Raises:
        typer.Exit: If no TTY and inference confidence is low.
    """
    transport, confident = infer_transport(target)

    if confident:
        console.print(f"[dim]Inferred transport: {transport} (override with --transport)[/dim]")
        return transport

    if is_tty():
        console.print(f"[yellow]Could not determine transport for '{target}'.[/yellow]")
        choice: str = typer.prompt(
            "Choose transport",
            type=click.Choice(_VALID_TRANSPORTS),
            default="streamable-http",
        )
        return choice

    console.print(f"[red]Error: could not infer transport for '{target}'.[/red]")
    console.print("[dim]Pass --transport explicitly.[/dim]")
    raise typer.Exit(code=1)


def parse_meta_flags(meta_values: list[str] | None) -> dict[str, str] | None:
    """Parse repeated --meta key=value flags into a dict.

    Args:
        meta_values: List of "key=value" strings, or None.

    Returns:
        Dict of metadata, or None if no values provided.

    Raises:
        typer.BadParameter: If any value is malformed.
    """
    if not meta_values:
        return None

    result: dict[str, str] = {}
    for item in meta_values:
        if "=" not in item:
            raise typer.BadParameter(f"Invalid --meta format: '{item}'. Expected key=value.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"Invalid --meta format: '{item}'. Key cannot be empty.")
        result[key] = value

    return result
