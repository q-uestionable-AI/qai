"""Shared state for IPI CLI subcommands.

Hosts the Typer ``app`` instance, the Rich ``console``, cross-cutting
constants used by more than one subcommand, and ``validate_format`` —
the one helper referenced outside ``generate``. Anything strictly
local to a single subcommand lives in that subcommand's module.
"""

from __future__ import annotations

import typer
from rich.console import Console

from q_ai.ipi.generators.docx import DOCX_TECHNIQUES as DOCX_TECHNIQUE_LIST
from q_ai.ipi.generators.eml import EML_TECHNIQUES as EML_TECHNIQUE_LIST
from q_ai.ipi.generators.html import HTML_TECHNIQUES as HTML_TECHNIQUE_LIST
from q_ai.ipi.generators.ics import ICS_TECHNIQUES as ICS_TECHNIQUE_LIST
from q_ai.ipi.generators.image import IMAGE_TECHNIQUES as IMAGE_TECHNIQUE_LIST
from q_ai.ipi.generators.markdown import MARKDOWN_TECHNIQUES as MARKDOWN_TECHNIQUE_LIST
from q_ai.ipi.generators.pdf import PDF_PHASE1_TECHNIQUES, PDF_PHASE2_TECHNIQUES
from q_ai.ipi.models import CitationFrame, Format

SUPPORTED_TUNNEL_PROVIDERS = ("cloudflare",)
"""Tunnel providers accepted by the ``--tunnel`` flag on ``listen``."""

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


def _parse_citation_frame(value: str) -> CitationFrame:
    """Parse --citation-frame. Accepts the two CitationFrame values.

    Shared by both ``qai ipi sweep`` and ``qai ipi generate`` so the flag
    has identical semantics on both commands (case-insensitive, whitespace
    tolerated, and ``typer.BadParameter`` on unknown values).

    Args:
        value: Frame name. One of ``"plain"`` or ``"template-aware"``
            (case-insensitive). Leading/trailing whitespace is tolerated.

    Returns:
        The resolved :class:`CitationFrame` enum.

    Raises:
        typer.BadParameter: If ``value`` is not a known frame.
    """
    normalized = value.strip().lower()
    for frame in CitationFrame:
        if frame.value == normalized:
            return frame
    valid = ", ".join(f"'{f.value}'" for f in CitationFrame)
    raise typer.BadParameter(f"--citation-frame must be one of {valid} (got {value!r}).")


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
        if fmt not in IMPLEMENTED_FORMATS:
            console.print(f"[red]X Format not yet implemented: {format_name_lower}[/red]")
            supported = ", ".join(f.value for f in IMPLEMENTED_FORMATS)
            console.print(f"  Currently supported: {supported}")
            planned = ", ".join(f.value for f in Format if f not in IMPLEMENTED_FORMATS)
            console.print(f"  Planned: {planned}")
            raise typer.Exit(1)
        return fmt
