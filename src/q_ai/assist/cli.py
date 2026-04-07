"""CLI subcommand for the qai assistant.

Provides single-shot, interactive REPL, piped input, and --run modes
for conversational AI assistance powered by RAG over qai documentation.

Commands:
    (default)   Ask a question or enter interactive mode
    reindex     Force rebuild of the knowledge base index

Usage:
    $ qai assist "what can qai test for?"
    $ qai assist
    $ echo '{"findings": [...]}' | qai assist "explain this"
    $ qai assist --run <run_id> "summarize findings"
    $ qai assist reindex
"""

import asyncio
import json
import sys
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(
    help="AI assistant — ask questions, interpret results, plan workflows.",
)
console = Console()


def _read_piped_stdin() -> str:
    """Read piped stdin if available.

    Returns:
        Piped input content, or empty string if stdin is a terminal.
    """
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def _load_run_context(run_id: str) -> str:
    """Load findings from a run as scan-derived context.

    Args:
        run_id: The run ID to pull findings from.

    Returns:
        JSON-formatted findings string.
    """
    from q_ai.core.db import get_connection, get_run, list_findings

    with get_connection() as conn:
        run = get_run(conn, run_id)
        if run is None:
            console.print(f"[red]Run not found: {run_id}[/red]")
            raise typer.Exit(1)

        findings = list_findings(conn, run_id=run_id)

    if not findings:
        return json.dumps({"run_id": run_id, "findings": [], "note": "No findings for this run."})

    return json.dumps(
        {
            "run_id": run_id,
            "module": run.module,
            "status": run.status.name,
            "findings": [f.to_dict() for f in findings],
        },
        indent=2,
        default=str,
    )


def _check_configured() -> None:
    """Check that the assistant is configured, exit with guidance if not."""
    from q_ai.assist.service import AssistantNotConfiguredError, _resolve_model_string

    try:
        _resolve_model_string()
    except AssistantNotConfiguredError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None


def _run_single_shot(query: str, scan_context: str) -> None:
    """Execute a single-shot query and print the response.

    Args:
        query: User question.
        scan_context: Optional untrusted scan context.
    """
    from q_ai.assist.service import chat_stream

    async def _do_query() -> None:
        async for token in chat_stream(query, scan_context=scan_context):
            console.print(token, end="", highlight=False)
        console.print()  # Final newline

    asyncio.run(_do_query())


def _run_interactive(scan_context: str) -> None:
    """Run the interactive REPL mode.

    Args:
        scan_context: Optional initial scan context.
    """
    from q_ai.assist.service import chat_stream

    console.print("[bold]qai assistant[/bold] — type your question, 'exit' to quit.\n")
    history: list[dict[str, str]] = []

    while True:
        try:
            query = console.input("[bold cyan]> [/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        query = query.strip()
        if not query:
            continue
        if query.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye.[/dim]")
            break

        async def _do_stream(q: str = query) -> str:
            full_response = ""
            async for token in chat_stream(q, scan_context=scan_context, history=history):
                console.print(token, end="", highlight=False)
                full_response += token
            console.print()
            return full_response

        try:
            response = asyncio.run(_do_stream())
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": response})
        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]")


@app.callback(invoke_without_command=True)
def assist_main(
    ctx: typer.Context,
    query: Annotated[
        str | None,
        typer.Argument(help="Question to ask the assistant."),
    ] = None,
    run: Annotated[
        str | None,
        typer.Option("--run", "-r", help="Pull findings from this run ID as context."),
    ] = None,
) -> None:
    """Ask the qai assistant a question or enter interactive mode.

    Without arguments, enters an interactive chat session.
    With a question argument, returns a single response and exits.
    Piped stdin is treated as untrusted scan-derived context.
    """
    if ctx.invoked_subcommand is not None:
        return

    _check_configured()

    # Build scan context from piped stdin and/or --run flag
    scan_context = ""
    piped = _read_piped_stdin()
    if piped.strip():
        scan_context = piped

    if run:
        run_context = _load_run_context(run)
        scan_context = f"{scan_context}\n\n{run_context}" if scan_context else run_context

    if query:
        _run_single_shot(query, scan_context)
    else:
        _run_interactive(scan_context)


@app.command()
def reindex() -> None:
    """Force rebuild of the knowledge base index.

    Re-scans all product documentation and user knowledge files,
    regenerates embeddings, and updates the ChromaDB collections.
    """
    from q_ai.assist.service import reindex as do_reindex

    console.print("[dim]Reindexing knowledge base...[/dim]")
    do_reindex(force=True)
    console.print("[green]Knowledge base reindexed successfully.[/green]")
