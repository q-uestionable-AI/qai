"""IPI subcommands — indirect prompt injection via document ingestion.

Commands:
    generate    Create document(s) with hidden prompt injection payloads
    probe       Test model susceptibility to indirect prompt injection
    techniques  List all available hiding techniques
    formats     List supported output formats
    listen      Start the callback listener server
    status      Check campaign status and hits
    export      Export campaigns and hits to JSON
    reset       Reset all campaigns, hits, and generated files

Usage:
    $ qai ipi generate --callback http://localhost:8080 --technique all
    $ qai ipi listen --port 8080
    $ qai ipi status
    $ qai ipi techniques

For detailed help on any command:
    $ qai ipi <command> --help
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from q_ai.ipi.probe_service import Probe, ProbeRunResult

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from q_ai.core.cli.prompt import build_teaching_tip, is_tty, prompt_or_fail
from q_ai.ipi import db
from q_ai.ipi.generate_service import GenerateResult, generate_documents
from q_ai.ipi.generators import ENCODING_CHOICES, get_techniques_for_format
from q_ai.ipi.generators.docx import DOCX_TECHNIQUES as DOCX_TECHNIQUE_LIST
from q_ai.ipi.generators.eml import EML_TECHNIQUES as EML_TECHNIQUE_LIST
from q_ai.ipi.generators.html import HTML_TECHNIQUES as HTML_TECHNIQUE_LIST
from q_ai.ipi.generators.ics import ICS_TECHNIQUES as ICS_TECHNIQUE_LIST
from q_ai.ipi.generators.image import IMAGE_TECHNIQUES as IMAGE_TECHNIQUE_LIST
from q_ai.ipi.generators.markdown import MARKDOWN_TECHNIQUES as MARKDOWN_TECHNIQUE_LIST
from q_ai.ipi.generators.pdf import PDF_PHASE1_TECHNIQUES, PDF_PHASE2_TECHNIQUES
from q_ai.ipi.models import (
    Campaign,
    DocumentTemplate,
    Format,
    Hit,
    HitConfidence,
    PayloadStyle,
    PayloadType,
    Technique,
)
from q_ai.ipi.server import start_server

app = typer.Typer(
    help="Indirect Prompt Injection — Generate payloads and detect AI agent execution",
    no_args_is_help=True,
)
console = Console()

# Technique presets for CLI parsing (string names for display)
PHASE1_TECHNIQUES = [t.value for t in PDF_PHASE1_TECHNIQUES]
"""Phase 1 technique names (basic hiding methods)."""

PHASE2_TECHNIQUES = [t.value for t in PDF_PHASE2_TECHNIQUES]
"""Phase 2 technique names (advanced hiding methods)."""

IMAGE_TECHNIQUES = [t.value for t in IMAGE_TECHNIQUE_LIST]
"""Image technique names (VLM attack surface)."""

MARKDOWN_TECHNIQUES = [t.value for t in MARKDOWN_TECHNIQUE_LIST]
"""Markdown technique names (document processing pipelines)."""

HTML_TECHNIQUES = [t.value for t in HTML_TECHNIQUE_LIST]
"""HTML technique names (web/document processing pipelines)."""

DOCX_TECHNIQUES = [t.value for t in DOCX_TECHNIQUE_LIST]
"""DOCX technique names (Word document processing pipelines)."""

ICS_TECHNIQUES = [t.value for t in ICS_TECHNIQUE_LIST]
"""ICS technique names (calendar invite processing pipelines)."""

EML_TECHNIQUES = [t.value for t in EML_TECHNIQUE_LIST]
"""EML technique names (email processing pipelines)."""

SUPPORTED_FORMATS = [f.value for f in Format]
"""Currently supported output formats."""

IMPLEMENTED_FORMATS = {
    Format.PDF,
    Format.IMAGE,
    Format.MARKDOWN,
    Format.HTML,
    Format.DOCX,
    Format.ICS,
    Format.EML,
}
"""Formats with working implementations."""

# Technique descriptions organized by (format_name, phase, technique_list)
_TECHNIQUE_SECTIONS: list[tuple[str, str, list[str], dict[str, str]]] = [
    (
        "pdf",
        "1",
        PHASE1_TECHNIQUES,
        {
            "white_ink": "White text on white background",
            "off_canvas": "Text at negative coordinates (off page)",
            "metadata": "Hidden in PDF metadata fields (Author, Subject, Keywords)",
        },
    ),
    (
        "pdf",
        "2",
        PHASE2_TECHNIQUES,
        {
            "tiny_text": "0.5pt font - below human visual threshold",
            "white_rect": "Text drawn then covered by white rectangle",
            "form_field": "Hidden AcroForm text field",
            "annotation": "PDF annotation/comment layer",
            "javascript": "PDF JavaScript (document-level)",
            "embedded_file": "Hidden file attachment stream",
            "incremental": "Payload in PDF incremental update section",
        },
    ),
    (
        "image",
        "3",
        IMAGE_TECHNIQUES,
        {
            "visible_text": "Human-readable text overlay on image",
            "subtle_text": "Low contrast, small font, edge-placed text",
            "exif_metadata": "Payload in EXIF metadata fields",
        },
    ),
    (
        "markdown",
        "3",
        MARKDOWN_TECHNIQUES,
        {
            "html_comment": "Payload in HTML comment tags (<!-- -->)",
            "link_reference": "Payload in unused link reference definition",
            "zero_width": "Payload encoded with zero-width Unicode chars",
            "hidden_block": "Payload in HTML div with display:none",
        },
    ),
    (
        "html",
        "3",
        HTML_TECHNIQUES,
        {
            "script_comment": "Payload in JavaScript comment inside script tag",
            "css_offscreen": "Payload in element positioned off-screen with CSS",
            "data_attribute": "Payload in HTML data-* attribute",
            "meta_tag": "Payload in HTML meta tag content",
        },
    ),
    (
        "docx",
        "3",
        DOCX_TECHNIQUES,
        {
            "docx_hidden_text": "Text with hidden font attribute (invisible)",
            "docx_tiny_text": "0.5pt font - below human visual threshold",
            "docx_white_text": "White text on white background",
            "docx_comment": "Payload in Word comment/annotation",
            "docx_metadata": "Payload in document core properties",
            "docx_header_footer": "Payload in document header or footer",
        },
    ),
    (
        "ics",
        "3",
        ICS_TECHNIQUES,
        {
            "ics_description": "Payload in event DESCRIPTION property",
            "ics_location": "Payload in event LOCATION property",
            "ics_valarm": "Payload in VALARM reminder DESCRIPTION",
            "ics_x_property": "Payload in custom X- extension property",
        },
    ),
    (
        "eml",
        "3",
        EML_TECHNIQUES,
        {
            "eml_x_header": "Payload in custom X- email header",
            "eml_html_hidden": "Payload in hidden HTML div (display:none)",
            "eml_attachment": "Payload in text file attachment",
        },
    ),
]


def parse_techniques(technique_str: str) -> list[Technique]:
    """Parse technique specification string into list of Technique enums.

    Supports preset names (all, phase1, phase2), individual technique names,
    or comma-separated lists of technique names.

    Args:
        technique_str: Technique specification. Options:
            - "all": All techniques from both phases
            - "phase1": WHITE_INK, OFF_CANVAS, METADATA
            - "phase2": TINY_TEXT, WHITE_RECT, FORM_FIELD, ANNOTATION,
                       JAVASCRIPT, EMBEDDED_FILE, INCREMENTAL
            - Single technique name (e.g., "white_ink")
            - Comma-separated names (e.g., "white_ink,metadata")

    Returns:
        List of Technique enum values.

    Raises:
        ValueError: If any technique name is invalid.
    """
    technique_str = technique_str.lower().strip()

    # Handle presets
    if technique_str == "all":
        # Technique.NONE is a control condition (visible payload), not a
        # hiding technique. Exclude it from batch presets; callers who want
        # the control must request it explicitly with --technique none.
        return [t for t in Technique if t is not Technique.NONE]
    if technique_str == "phase1":
        return [Technique(t) for t in PHASE1_TECHNIQUES]
    if technique_str == "phase2":
        return [Technique(t) for t in PHASE2_TECHNIQUES]

    # Handle comma-separated list or single technique
    technique_names = [t.strip() for t in technique_str.split(",")]
    techniques = []

    for name in technique_names:
        try:
            techniques.append(Technique(name))
        except ValueError:
            raise ValueError(f"Invalid technique: {name}") from None

    return techniques


def validate_format(format_name: str) -> Format:
    """Validate that a format name is supported.

    Args:
        format_name: Format name to validate (case-insensitive).

    Returns:
        Format enum if valid.

    Raises:
        typer.Exit: If format is not supported (exits with code 1).
    """
    format_name_lower = format_name.lower().strip()
    try:
        fmt = Format(format_name_lower)
    except ValueError:
        console.print(f"[red]X Unknown format: {format_name_lower}[/red]")
        console.print(f"  Valid formats: {', '.join(f.value for f in Format)}")
        raise typer.Exit(1) from None
    else:
        # Check if format is actually implemented
        if fmt not in IMPLEMENTED_FORMATS:
            console.print(f"[red]X Format not yet implemented: {format_name_lower}[/red]")
            supported = ", ".join(f.value for f in IMPLEMENTED_FORMATS)
            console.print(f"  Currently supported: {supported}")
            planned = ", ".join(f.value for f in Format if f not in IMPLEMENTED_FORMATS)
            console.print(f"  Planned: {planned}")
            raise typer.Exit(1)
        return fmt


def _parse_payload_style(payload_style: str) -> PayloadStyle:
    """Parse and validate a payload style string.

    Args:
        payload_style: Raw payload style name from CLI input.

    Returns:
        Validated PayloadStyle enum value.

    Raises:
        typer.Exit: If the payload style is invalid.
    """
    try:
        return PayloadStyle(payload_style)
    except ValueError:
        console.print(f"[red]X Invalid payload style: {payload_style}[/red]")
        console.print(f"  Valid options: {', '.join(p.value for p in PayloadStyle)}")
        raise typer.Exit(1) from None


def _parse_payload_type(payload_type: str) -> PayloadType:
    """Parse and validate a payload type string.

    Args:
        payload_type: Raw payload type name from CLI input.

    Returns:
        Validated PayloadType enum value.

    Raises:
        typer.Exit: If the payload type is invalid.
    """
    try:
        return PayloadType(payload_type)
    except ValueError:
        console.print(f"[red]X Invalid payload type: {payload_type}[/red]")
        console.print(f"  Valid options: {', '.join(p.value for p in PayloadType)}")
        raise typer.Exit(1) from None


def _parse_encoding(encoding: str) -> str:
    """Validate a payload URL encoding choice.

    Args:
        encoding: Raw encoding name from CLI input.

    Returns:
        The validated encoding string (one of ``ENCODING_CHOICES``).

    Raises:
        typer.Exit: If the encoding is not recognized.
    """
    if encoding not in ENCODING_CHOICES:
        console.print(f"[red]X Invalid encoding: {encoding}[/red]")
        console.print(f"  Valid options: {', '.join(ENCODING_CHOICES)}")
        raise typer.Exit(1)
    return encoding


def _enforce_dangerous_gate(payload_type_enum: PayloadType, dangerous: bool) -> None:
    """Enforce the --dangerous safety gate for non-callback payload types.

    Args:
        payload_type_enum: The resolved payload type.
        dangerous: Whether the --dangerous flag was provided.

    Raises:
        typer.Exit: If a dangerous payload type is used without --dangerous.
    """
    if payload_type_enum == PayloadType.CALLBACK:
        return

    if not dangerous:
        console.print(
            f"[red]X Payload type '{payload_type_enum.value}' requires --dangerous flag[/red]"
        )
        console.print("  Non-callback payloads can cause real harm to target systems.")
        console.print("  Use [bold]--dangerous[/bold] to confirm authorized testing.")
        raise typer.Exit(1)

    console.print()
    console.print("[bold red]" + "=" * 60 + "[/bold red]")
    console.print("[bold red]  WARNING: DANGEROUS PAYLOAD TYPE ENABLED[/bold red]")
    console.print(f"[bold red]  Type: {payload_type_enum.value}[/bold red]")
    console.print("[bold red]  For authorized security testing only.[/bold red]")
    console.print("[bold red]" + "=" * 60 + "[/bold red]")
    console.print()


def _resolve_techniques(technique: str, format_name: Format) -> list[Technique]:
    """Parse technique string and filter to those valid for the given format.

    Args:
        technique: Technique specification string (preset or comma-separated names).
        format_name: Target format for filtering available techniques.

    Returns:
        List of validated Technique enums applicable to the format.

    Raises:
        typer.Exit: If parsing fails or no valid techniques remain.
    """
    try:
        techniques = parse_techniques(technique)
    except ValueError as e:
        console.print(f"[red]X {e}[/red]")
        console.print("  Valid presets: all, phase1, phase2")
        console.print(f"  Valid techniques: {', '.join(t.value for t in Technique)}")
        raise typer.Exit(1) from None

    format_techniques = get_techniques_for_format(format_name, include_none=True)
    valid_techniques = [t for t in techniques if t in format_techniques]

    if not valid_techniques:
        console.print(f"[red]X No valid techniques for format '{format_name.value}'[/red]")
        console.print(f"  Available techniques: {', '.join(t.value for t in format_techniques)}")
        raise typer.Exit(1)

    if len(valid_techniques) < len(techniques):
        skipped = [t for t in techniques if t not in format_techniques]
        skipped_names = ", ".join(t.value for t in skipped)
        console.print(
            f"[yellow]! Skipping techniques not available"
            f" for {format_name.value}: {skipped_names}[/yellow]"
        )

    return valid_techniques


def _display_generate_results(
    result: GenerateResult,
    format_name: Format,
    style: PayloadStyle,
    payload_type_enum: PayloadType,
    callback_url: str,
) -> None:
    """Display generation results to the console.

    Args:
        result: GenerateResult with campaigns and errors lists.
        format_name: Format used for generation.
        style: Payload style used.
        payload_type_enum: Payload type used.
        callback_url: Callback URL for display.
    """
    if len(result.campaigns) > 1:
        console.print(
            f"\n[bold green]OK Generated {len(result.campaigns)} "
            f"{format_name.value.upper()} files "
            f"({style.value} payload, {payload_type_enum.value} type):[/bold green]"
        )
        for c in result.campaigns:
            console.print(f"  - {c.filename} ({c.technique}) -> UUID: [cyan]{c.uuid}[/cyan]")
    elif result.campaigns:
        c = result.campaigns[0]
        console.print(f"\n[bold green]OK Generated:[/bold green] {c.filename}")
        console.print(f"  Format: {format_name.value}")
        console.print(f"  Technique: {c.technique}")
        console.print(f"  Payload Style: {style.value}")
        console.print(f"  Payload Type: {payload_type_enum.value}")
        console.print(f"  UUID: [cyan]{c.uuid}[/cyan]")

    for err in result.errors:
        console.print(f"  [yellow]! {err}[/yellow]")

    console.print(f"\n[dim]Callback URL: {callback_url}/c/<uuid>[/dim]")


@app.command(
    epilog=(
        "Examples:\n"
        "  qai ipi generate http://localhost:8080\n"
        "  qai ipi generate http://localhost:8080 --format image --technique all\n"
        "  qai ipi generate --callback http://localhost:8080 --technique phase1\n"
        "  qai ipi generate  (interactive — prompts for callback URL)"
    ),
)
def generate(
    callback: Annotated[
        str | None,
        typer.Argument(
            help="Callback server URL (prompted interactively if omitted).",
        ),
    ] = None,
    callback_option: Annotated[
        str | None,
        typer.Option("--callback", "-c", help="Callback server URL (alternative to positional)."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output path (file or directory, default: ~/.qai/payloads/)",
        ),
    ] = Path.home() / ".qai" / "payloads",
    format_name: Annotated[
        str, typer.Option("--format", help="Output format (default: pdf)")
    ] = "pdf",
    technique: Annotated[
        str,
        typer.Option(
            "--technique",
            "-t",
            help="Technique(s): all, phase1, phase2, or specific names (comma-separated)",
        ),
    ] = "all",
    payload_type: Annotated[
        str,
        typer.Option(
            "--payload-type",
            help=(
                "Payload type: callback, exfil_summary, exfil_context, ssrf_internal, "
                "instruction_override, tool_abuse, persistence"
            ),
        ),
    ] = "callback",
    payload_style: Annotated[
        str,
        typer.Option(
            "--payload",
            "--payload-style",
            "-p",
            help="Payload style: obvious, citation, reviewer, "
            "helpful, academic, compliance, datasource",
        ),
    ] = "obvious",
    name: Annotated[str, typer.Option("--name", "-n", help="Base filename")] = "report",
    dangerous: Annotated[
        bool,
        typer.Option(
            "--dangerous",
            help="Enable non-callback payload types (exfil, ssrf, override, etc.)",
        ),
    ] = False,
    seed: Annotated[
        int | None,
        typer.Option(
            "--seed",
            help="Seed for deterministic UUID/token generation (reproducible corpus).",
        ),
    ] = None,
    encoding: Annotated[
        str,
        typer.Option(
            "--encoding",
            help="Encode payload text (none=plaintext, base16/hex=obfuscated)",
        ),
    ] = "none",
    template: Annotated[
        DocumentTemplate,
        typer.Option(
            "--template",
            help="Document context template for payload framing (default: generic).",
            case_sensitive=False,
        ),
    ] = DocumentTemplate.GENERIC,
) -> None:
    """Generate document(s) with hidden prompt injection payload.

    CALLBACK can be provided as the first positional argument, via
    --callback, or entered interactively when running in a terminal.

    Creates one or more documents containing hidden prompt injection
    payloads using the specified technique(s). Each generated document
    is registered in the database for callback tracking.

    Technique options:

    \b
    Presets:
      all     - All techniques (Phase 1 + Phase 2)
      phase1  - white_ink, off_canvas, metadata
      phase2  - tiny_text, white_rect, form_field, annotation,
                javascript, embedded_file, incremental

    \b
    Individual (or comma-separated):
      white_ink      - White text on white background (Phase 1)
      off_canvas     - Text at negative coordinates (Phase 1)
      metadata       - Hidden in PDF metadata fields (Phase 1)
      tiny_text      - 0.5pt font, below visual threshold (Phase 2)
      white_rect     - Text covered by white rectangle (Phase 2)
      form_field     - Hidden AcroForm field (Phase 2)
      annotation     - PDF annotation/comment layer (Phase 2)
      javascript     - PDF JavaScript action (Phase 2)
      embedded_file  - Hidden file attachment (Phase 2)
      incremental    - PDF incremental update section (Phase 2)
    """
    # Resolve callback: positional > --callback > prompt
    provided_directly = callback is not None or callback_option is not None
    raw_callback = callback or callback_option or None
    callback_url = prompt_or_fail("CALLBACK", raw_callback, "Callback server URL")

    if not provided_directly and is_tty():
        tip = build_teaching_tip("qai ipi generate", [callback_url])
        console.print(f"[dim]{tip}[/dim]")

    format_name = validate_format(format_name)
    style = _parse_payload_style(payload_style)
    payload_type_enum = _parse_payload_type(payload_type)
    encoding = _parse_encoding(encoding)
    _enforce_dangerous_gate(payload_type_enum, dangerous)
    techniques = _resolve_techniques(technique, format_name)

    # Generate documents via shared service
    result = generate_documents(
        callback_url=callback_url,
        output=output,
        format_name=format_name,
        techniques=techniques,
        payload_style=style,
        payload_type=payload_type_enum,
        base_name=name,
        seed=seed,
        encoding=encoding,
        template=template,
    )

    # Persist to core DB via mapper
    from q_ai.ipi.mapper import persist_generate

    if result.campaigns:
        persist_generate(result.campaigns, source="cli")

    _display_generate_results(result, format_name, style, payload_type_enum, callback_url)


@app.command(
    epilog=(
        "Examples:\n"
        "  qai ipi probe http://localhost:8000/v1 --model my-model\n"
        "  qai ipi probe http://localhost:8000/v1 -m my-model --concurrency 4\n"
        "  qai ipi probe --dry-run\n"
        "  qai ipi probe  (interactive — prompts for endpoint and model)"
    ),
)
def probe(
    endpoint_url: Annotated[
        str | None,
        typer.Argument(
            help="OpenAI-compatible API base URL (prompted interactively if omitted).",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model name for chat completions."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            help="Bearer token for authentication (also reads QAI_PROBE_API_KEY env var).",
        ),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", "-t", help="Target ID to associate results with."),
    ] = None,
    temperature: Annotated[
        float,
        typer.Option("--temperature", help="Sampling temperature (default 0.0)."),
    ] = 0.0,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Max parallel probe requests (default 1)."),
    ] = 1,
    probe_set: Annotated[
        Path | None,
        typer.Option("--probe-set", help="Custom YAML probe file (overrides built-in)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List probes without sending requests."),
    ] = False,
    export: Annotated[
        Path | None,
        typer.Option("--export", "-o", help="Write raw results JSON to this path."),
    ] = None,
) -> None:
    """Test model susceptibility to indirect prompt injection.

    Sends a set of probe prompts to an OpenAI-compatible chat completions
    endpoint and scores responses for canary compliance. Results are
    persisted to the qai database as findings.

    ENDPOINT_URL is the API base URL (e.g. http://localhost:8000/v1);
    the service appends /chat/completions automatically.
    """
    from q_ai.ipi.probe_service import (
        export_scored_prompts,
        load_probes,
        persist_probe_run,
        run_probes,
    )

    # Load probes early — dry-run needs them but not endpoint/model.
    try:
        probes = load_probes(probe_set)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading probes: {exc}[/red]")
        raise typer.Exit(1) from None

    # Dry run — display probes and exit (no endpoint/model needed).
    if dry_run:
        _display_probe_dry_run(probes)
        raise typer.Exit()

    # Resolve endpoint: positional > prompt
    provided_directly = endpoint_url is not None
    resolved_endpoint = prompt_or_fail("ENDPOINT_URL", endpoint_url, "API base URL")

    # Resolve model: --model > prompt
    model_provided_directly = model is not None
    resolved_model = prompt_or_fail("MODEL", model, "Model name")

    if (not provided_directly or not model_provided_directly) and is_tty():
        tip_args = [resolved_endpoint, "--model", resolved_model]
        tip = build_teaching_tip("qai ipi probe", tip_args)
        console.print(f"[dim]{tip}[/dim]")

    # Resolve API key: --api-key > env var
    resolved_api_key = api_key or os.environ.get("QAI_PROBE_API_KEY")

    if concurrency < 1:
        console.print("[red]Error: --concurrency must be >= 1[/red]")
        raise typer.Exit(1)

    # Execute probes
    console.print(
        f"[bold]Running {len(probes)} probes against {resolved_model}"
        f" at {resolved_endpoint}...[/bold]"
    )
    run_result = asyncio.run(
        run_probes(
            endpoint=resolved_endpoint,
            model=resolved_model,
            probes=probes,
            api_key=resolved_api_key,
            temperature=temperature,
            concurrency=concurrency,
        )
    )

    # Persist to DB
    run_id = persist_probe_run(
        run_result=run_result,
        model=resolved_model,
        endpoint=resolved_endpoint,
        target_id=target,
    )

    # Export if requested
    if export:
        actual_path = export_scored_prompts(run_result, resolved_model, resolved_endpoint, export)
        console.print(f"[green]Exported results to {actual_path}[/green]")

    # Display results
    _display_probe_results(run_result, run_id)


def _display_probe_dry_run(probes: list[Probe]) -> None:
    """Display a table of probes for dry-run mode.

    Args:
        probes: List of Probe objects to display.
    """
    table = Table(title="IPI Probes (dry run)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("Description")

    for idx, p in enumerate(probes, start=1):
        table.add_row(str(idx), p.id, p.category, p.description)

    console.print(table)
    console.print(f"\n[bold]{len(probes)}[/bold] probes would be sent.")


def _display_probe_results(run_result: ProbeRunResult, run_id: str) -> None:
    """Display probe results as a Rich table with per-category breakdown.

    Args:
        run_result: ProbeRunResult with category_stats and totals.
        run_id: The database run ID for display.
    """
    severity_colors = {
        "INFO": "dim",
        "LOW": "green",
        "MEDIUM": "yellow",
        "HIGH": "red",
        "CRITICAL": "bold red",
    }

    table = Table(title="IPI Probe Results")
    table.add_column("Category", style="magenta")
    table.add_column("Probes", justify="center")
    table.add_column("Complied", justify="center")
    table.add_column("Rate", justify="center")
    table.add_column("Severity", justify="center")

    for stat in run_result.category_stats:
        sev_name = stat.severity.name
        sev_color = severity_colors.get(sev_name, "white")
        table.add_row(
            stat.category,
            str(stat.total),
            str(stat.complied),
            f"{stat.rate:.0%}",
            f"[{sev_color}]{sev_name}[/{sev_color}]",
        )

    # Total row
    total = run_result.total_probes
    complied = run_result.total_complied
    rate = run_result.overall_rate
    sev_name = run_result.overall_severity.name
    sev_color = severity_colors.get(sev_name, "white")
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total}[/bold]",
        f"[bold]{complied}[/bold]",
        f"[bold]{rate:.0%}[/bold]",
        f"[bold][{sev_color}]{sev_name}[/{sev_color}][/bold]",
    )

    console.print(table)
    console.print(f"\n[dim]Run ID: {run_id}[/dim]")


@app.command()
def techniques(
    format_name: Annotated[
        str | None,
        typer.Option("--format", "-f", help="Filter by format (pdf, image, markdown)"),
    ] = None,
) -> None:
    """List all available hiding techniques.

    Displays a table of all supported payload hiding techniques,
    organized by format and phase with descriptions.
    """
    table = Table(title="IPI Hiding Techniques")
    table.add_column("Format", style="magenta")
    table.add_column("Phase", style="cyan")
    table.add_column("Technique", style="green")
    table.add_column("Description")

    for fmt_name, phase, tech_list, desc in _TECHNIQUE_SECTIONS:
        if format_name is None or format_name.lower() == fmt_name:
            for tech in tech_list:
                table.add_row(fmt_name, phase, tech, desc.get(tech, ""))

    console.print(table)
    console.print(
        "\n[dim]Use --technique with: all, phase1, phase2, or comma-separated names[/dim]"
    )
    console.print(
        "[dim]Use --format to filter by format (pdf, image, markdown, html, docx, ics, eml)[/dim]"
    )


@app.command()
def formats() -> None:
    """List supported output formats.

    Displays a table of all document formats with implementation status.
    """
    table = Table(title="IPI Formats")
    table.add_column("Format", style="green")
    table.add_column("Status")
    table.add_column("Techniques")

    for fmt in Format:
        if fmt in IMPLEMENTED_FORMATS:
            status = "[green]available[/green]"
            fmt_techniques = get_techniques_for_format(fmt, include_none=True)
            tech_count = f"{len(fmt_techniques)} techniques"
        else:
            status = "[dim]planned[/dim]"
            tech_count = "-"
        table.add_row(fmt.value, status, tech_count)

    console.print(table)


@app.command()
def listen(
    port: Annotated[int, typer.Option("--port", "-p", help="Port to listen on")] = 8080,
    host: Annotated[str, typer.Option("--host", "-h", help="Host to bind to")] = "127.0.0.1",
    notify_url: Annotated[
        str,
        typer.Option("--notify-url", help="Main qai server URL for hit notifications"),
    ] = "http://127.0.0.1:8899",
) -> None:
    """Start the callback listener server.

    Launches the FastAPI server that receives and logs callback
    requests from AI agents that execute the hidden payloads.
    """
    start_server(host=host, port=port, notify_url=notify_url)


def _display_single_campaign(campaign: Campaign, hits: list[Hit]) -> None:
    """Render detailed status output for a single campaign.

    Args:
        campaign: The campaign to display.
        hits: List of hits associated with this campaign.
    """
    console.print(f"\n[bold]Campaign:[/bold] {escape(campaign.uuid)}")
    console.print(f"  File: {escape(campaign.filename)}")
    console.print(f"  Format: {campaign.format}")
    console.print(f"  Technique: {campaign.technique}")
    console.print(f"  Payload Style: {campaign.payload_style}")
    console.print(f"  Payload Type: {campaign.payload_type}")
    console.print(f"  Created: {campaign.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

    if not hits:
        console.print("\n[dim]No hits recorded[/dim]")
        return

    console.print(f"\n[bold green]Hit {len(hits)} hit(s):[/bold green]")
    for hit in hits:
        ts = hit.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        token_icon = "+" if hit.token_valid else "x"
        conf = hit.confidence.value
        console.print(
            f"  * {ts} from {escape(hit.source_ip)}  [token:{token_icon}] [confidence:{conf}]"
        )


def _filter_campaigns(
    campaigns: list[Campaign],
    format_name: str | None,
    technique: str | None,
    payload_type: str | None,
) -> list[Campaign]:
    """Apply optional filters to a list of campaigns.

    Args:
        campaigns: Full list of campaigns to filter.
        format_name: If provided, filter by this format name.
        technique: If provided, filter by this technique name.
        payload_type: If provided, filter by this payload type name.

    Returns:
        Filtered list of campaigns.

    Raises:
        typer.Exit: If a filter value is invalid.
    """
    if format_name:
        validated_format = validate_format(format_name)
        campaigns = [c for c in campaigns if c.format == validated_format]

    if technique:
        try:
            technique_enum = Technique(technique)
        except ValueError:
            console.print(f"[red]X Invalid technique: {technique}[/red]")
            console.print(f"  Valid techniques: {', '.join(t.value for t in Technique)}")
            raise typer.Exit(1) from None
        campaigns = [c for c in campaigns if c.technique == technique_enum]

    if payload_type:
        try:
            payload_type_enum = PayloadType(payload_type)
        except ValueError:
            console.print(f"[red]X Invalid payload type: {payload_type}[/red]")
            console.print(f"  Valid options: {', '.join(p.value for p in PayloadType)}")
            raise typer.Exit(1) from None
        campaigns = [c for c in campaigns if c.payload_type == payload_type_enum]

    return campaigns


def _confidence_summary(campaign_hits: list[Hit]) -> str:
    """Build a Rich-formatted confidence breakdown string (H/M/L counts).

    Args:
        campaign_hits: List of hits for a single campaign.

    Returns:
        Rich markup string showing high/medium/low confidence counts,
        or a dimmed dash if no hits.
    """
    if not campaign_hits:
        return "[dim]-[/dim]"
    high = sum(1 for h in campaign_hits if h.confidence == HitConfidence.HIGH)
    med = sum(1 for h in campaign_hits if h.confidence == HitConfidence.MEDIUM)
    low = sum(1 for h in campaign_hits if h.confidence == HitConfidence.LOW)
    return f"[green]{high}H[/green]/[yellow]{med}M[/yellow]/[red]{low}L[/red]"


def _display_campaigns_table(
    campaigns: list[Campaign],
    hits_by_uuid: dict[str, list[Hit]],
) -> None:
    """Render a Rich table of all campaigns with hit summaries.

    Args:
        campaigns: List of campaigns to display.
        hits_by_uuid: Mapping from campaign UUID to its list of hits.
    """
    table = Table(title="IPI Campaigns")
    table.add_column("UUID", style="cyan", no_wrap=True)
    table.add_column("File")
    table.add_column("Format")
    table.add_column("Technique")
    table.add_column("Payload Style")
    table.add_column("Payload Type")
    table.add_column("Hits", justify="center")
    table.add_column("Confidence", justify="center")
    table.add_column("Created")

    for c in campaigns:
        campaign_hits = hits_by_uuid.get(c.uuid, [])
        hit_count = len(campaign_hits)
        hit_style = "bold green" if hit_count > 0 else "dim"

        table.add_row(
            escape(c.uuid[:8] + "..."),
            escape(c.filename),
            escape(c.format),
            escape(c.technique),
            escape(c.payload_style),
            escape(c.payload_type),
            f"[{hit_style}]{hit_count}[/{hit_style}]",
            _confidence_summary(campaign_hits),
            c.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)
    console.print("\n[dim]Use 'qai ipi status <uuid>' for details[/dim]")


@app.command()
def status(
    uuid: Annotated[str | None, typer.Argument(help="Campaign UUID (optional)")] = None,
    format_name: Annotated[str | None, typer.Option("--format", help="Filter by format")] = None,
    technique: Annotated[
        str | None, typer.Option("--technique", help="Filter by technique")
    ] = None,
    payload_type: Annotated[
        str | None, typer.Option("--payload-type", help="Filter by payload type")
    ] = None,
) -> None:
    """Check status of campaigns and hits.

    Without arguments, displays a table of all campaigns with hit counts.
    With a UUID argument, shows detailed information for that campaign
    including all recorded hits.

    Supports filtering by format, technique, and payload type.
    """
    if uuid:
        campaign = db.get_campaign(uuid)
        if not campaign:
            console.print(f"[red]X Campaign not found: {uuid}[/red]")
            raise typer.Exit(1)
        _display_single_campaign(campaign, db.get_hits(uuid))
        return

    campaigns = _filter_campaigns(db.get_all_campaigns(), format_name, technique, payload_type)

    if not campaigns:
        if format_name or technique or payload_type:
            console.print("[dim]No campaigns match the provided filters.[/dim]")
        else:
            console.print("[dim]No campaigns found. Run 'qai ipi generate' first.[/dim]")
        return

    all_hits = db.get_hits()
    hits_by_uuid: dict[str, list[Hit]] = {}
    for hit in all_hits:
        hits_by_uuid.setdefault(hit.uuid, []).append(hit)

    _display_campaigns_table(campaigns, hits_by_uuid)


def _build_ipi_interpret_prompt(campaigns: list, hits: list) -> str:
    """Assemble an AI-evaluation prompt from IPI export data.

    Args:
        campaigns: List of campaign objects with format and technique attributes.
        hits: List of hit objects.

    Returns:
        Prompt string ready for embedding in the export JSON.
    """
    n = len(campaigns)
    hit_count = len(hits)

    formats: list[str] = []
    techniques: list[str] = []
    for c in campaigns:
        f = getattr(c, "format", "")
        t = getattr(c, "technique", "")
        if f and f not in formats:
            formats.append(f)
        if t and t not in techniques:
            techniques.append(t)

    formats_str = ", ".join(formats) if formats else "multiple formats"
    techniques_str = ", ".join(techniques) if techniques else "multiple techniques"
    doc_str = f"{n} payload document{'s' if n != 1 else ''}"

    if n == 0:
        return "No payload documents generated."

    return (
        f"{doc_str} generated across {formats_str} "
        f"using {techniques_str}. "
        f"{hit_count} callback execution{'s' if hit_count != 1 else ''} recorded. "
        "Assess execution rates by technique and format, and evaluate "
        "detection risk for your target environment."
    )


@app.command()
def export(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output file (default: ~/.qai/exports/tracking.json)",
        ),
    ] = Path.home() / ".qai" / "exports" / "tracking.json",
) -> None:
    """Export campaigns and hits to JSON.

    Exports all campaign and hit data to a JSON file for external
    analysis, reporting, or backup purposes.
    """
    from q_ai.ipi.probe_service import get_unique_path, resolve_export_path

    output = resolve_export_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = get_unique_path(output)
    campaigns = db.get_all_campaigns()
    all_hits = db.get_hits()

    data = {
        "prompt": _build_ipi_interpret_prompt(campaigns, all_hits),
        "campaigns": [
            {
                "uuid": c.uuid,
                "filename": c.filename,
                "format": c.format,
                "technique": c.technique,
                "payload_style": c.payload_style,
                "payload_type": c.payload_type,
                "callback_url": c.callback_url,
                "created_at": c.created_at.isoformat(),
            }
            for c in campaigns
        ],
        "hits": [
            {
                "uuid": h.uuid,
                "source_ip": h.source_ip,
                "user_agent": h.user_agent,
                "timestamp": h.timestamp.isoformat(),
            }
            for h in all_hits
        ],
    }

    output.write_text(json.dumps(data, indent=2))
    console.print(f"[green]OK Exported to {output}[/green]")


@app.command()
def reset(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
) -> None:
    """Reset all campaigns, hits, and generated files.

    Deletes all campaign and hit records from the database and removes
    generated payload files from disk.

    Args:
        yes: Skip the confirmation prompt.
    """
    campaigns = db.get_all_campaigns()
    hits = db.get_hits()

    if not campaigns and not hits:
        console.print("[dim]Nothing to reset — database is already empty.[/dim]")
        return

    console.print(f"[bold]This will delete {len(campaigns)} campaigns and {len(hits)} hits.[/bold]")

    if not yes:
        confirm = typer.confirm("Are you sure?")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit()

    campaigns_deleted, hits_deleted, files_deleted = db.reset_db()
    console.print(
        f"[green]Done — removed {campaigns_deleted} campaigns, "
        f"{hits_deleted} hits, and {files_deleted} files.[/green]"
    )
