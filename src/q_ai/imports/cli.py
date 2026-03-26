"""CLI command for importing external tool results."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from q_ai.core.db import (
    create_evidence,
    create_finding,
    create_run,
    get_connection,
    update_run_status,
)
from q_ai.core.models import RunStatus
from q_ai.imports.garak import parse_garak
from q_ai.imports.models import ImportResult
from q_ai.imports.pyrit import parse_pyrit
from q_ai.imports.sarif import parse_sarif

logger = logging.getLogger(__name__)

console = Console()

_PARSERS = {
    "garak": parse_garak,
    "pyrit": parse_pyrit,
    "sarif": parse_sarif,
}

_EVIDENCE_TYPE_RAW = "import_raw"
_EVIDENCE_TYPE_METADATA = "import_metadata"


def _file_checksum(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _print_dry_run(result: ImportResult) -> None:
    """Display a summary of what would be imported."""
    table = Table(title=f"Dry Run — {result.tool_name} ({result.source_file})")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Severity")
    table.add_column("Category", no_wrap=True)
    table.add_column("Title")

    for idx, f in enumerate(result.findings, start=1):
        table.add_row(str(idx), f.severity.name, f.category, f.title)

    console.print(table)
    console.print(f"\n[bold]{len(result.findings)}[/bold] findings would be imported.")
    if result.errors:
        console.print(f"[yellow]{len(result.errors)} parse warning(s):[/yellow]")
        for err in result.errors:
            console.print(f"  - {err}")


def _persist(result: ImportResult, db_path: Path | None, source_file: Path) -> str:
    """Write parsed findings to the database.

    Args:
        result: Parsed import result from a parser module.
        db_path: Override database path, or ``None`` for the default.
        source_file: Path to the original import file (for checksum).

    Returns:
        The import run ID.
    """
    checksum = _file_checksum(source_file)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    run_name = f"{result.tool_name}-import-{ts}"

    with get_connection(db_path) as conn:
        run_id = create_run(
            conn,
            module="import",
            name=run_name,
            source=result.tool_name,
            config={
                "importer_version": result.parser_version,
                "tool_version": result.tool_version,
                "source_file": result.source_file,
                "source_checksum": checksum,
            },
        )

        for finding in result.findings:
            create_finding(
                conn,
                run_id=run_id,
                module=finding.source_tool,
                category=finding.category,
                severity=finding.severity,
                title=finding.title,
                description=finding.description,
                framework_ids=finding.original_taxonomy if finding.original_taxonomy else None,
                source_ref=finding.original_id,
            )

        # Raw evidence — summary of all imported data.
        raw_items: list[object] = []
        for f in result.findings:
            if f.raw_evidence:
                try:
                    raw_items.append(json.loads(f.raw_evidence))
                except json.JSONDecodeError:
                    raw_items.append({"_unparseable": f.raw_evidence})
        raw_summary = json.dumps(raw_items, indent=2, default=str)
        create_evidence(
            conn,
            type=_EVIDENCE_TYPE_RAW,
            run_id=run_id,
            storage="inline",
            content=raw_summary,
        )

        # Metadata evidence — provenance record.
        metadata = json.dumps(
            {
                "tool_name": result.tool_name,
                "tool_version": result.tool_version,
                "parser_version": result.parser_version,
                "source_file": result.source_file,
                "source_checksum": checksum,
                "finding_count": len(result.findings),
                "error_count": len(result.errors),
                "errors": result.errors,
            }
        )
        create_evidence(
            conn,
            type=_EVIDENCE_TYPE_METADATA,
            run_id=run_id,
            storage="inline",
            content=metadata,
        )

        update_run_status(conn, run_id, RunStatus.COMPLETED)

    return run_id


def import_cmd(
    file: Path = typer.Argument(
        ...,
        help="Path to the import file.",
        exists=True,
        readable=True,
    ),
    fmt: str = typer.Option(
        ...,
        "--format",
        "-f",
        help="Source format: garak, pyrit, or sarif.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse and show what would be imported without writing to DB.",
    ),
    db_path: Path | None = typer.Option(None, hidden=True),
) -> None:
    """Import findings from an external tool report.

    Parses a report file produced by Garak, PyRIT, or a SARIF-producing
    tool, normalizes findings with taxonomy bridging, and persists them
    to the qai database under a parent run with ``module=import``.

    Args:
        file: Path to the external tool report file.
        fmt: Source format identifier — ``"garak"``, ``"pyrit"``, or
            ``"sarif"``.
        dry_run: When ``True``, parse and display what would be imported
            without writing to the database.
        db_path: Override database path (hidden; used for testing).

    Raises:
        typer.Exit: With code 1 on unsupported format or parse failure.
    """
    parser = _PARSERS.get(fmt)
    if parser is None:
        console.print(
            f"[red]Unknown format '{fmt}'. Supported: {', '.join(sorted(_PARSERS))}[/red]"
        )
        raise typer.Exit(code=1)

    try:
        result = parser(file)
    except (ValueError, TypeError, OSError) as exc:
        console.print(f"[red]Import failed: {exc}[/red]")
        raise typer.Exit(code=1) from None

    if dry_run:
        _print_dry_run(result)
        raise typer.Exit()

    run_id = _persist(result, db_path, file)

    console.print(f"[green]Imported {len(result.findings)} findings.[/green]")
    console.print(f"  Run ID: {run_id}")
    console.print(f"  Tool:   {result.tool_name} {result.tool_version or '(version unknown)'}")
    if result.errors:
        console.print(f"  [yellow]{len(result.errors)} parse warning(s):[/yellow]")
        for err in result.errors:
            console.print(f"    - {err}")
