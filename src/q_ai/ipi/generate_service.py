"""Shared generation logic for IPI.

This module extracts the document generation dispatch logic into a reusable
service layer that can be called from both the CLI and the web API. It handles
format routing, technique filtering, campaign persistence, and batch generation.

Usage:
    From the CLI:

    >>> from q_ai.ipi.generate_service import generate_documents
    >>> result = generate_documents(
    ...     callback_url="http://localhost:8080",
    ...     output=Path("./payloads/"),
    ...     format_name=Format.PDF,
    ...     techniques=[Technique.WHITE_INK],
    ...     payload_style=PayloadStyle.CITATION,
    ...     payload_type=PayloadType.CALLBACK,
    ...     base_name="report",
    ... )

    From the API:

    >>> result = generate_documents(...)
    >>> for campaign in result.campaigns:
    ...     print(campaign.uuid)
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from q_ai.ipi import db
from q_ai.ipi.models import Campaign, Format, PayloadStyle, PayloadType, Technique

from .generators.docx import create_all_docx_variants, create_docx
from .generators.eml import create_all_eml_variants, create_eml
from .generators.html import create_all_html_variants, create_html
from .generators.ics import create_all_ics_variants, create_ics
from .generators.image import create_all_image_variants, create_image
from .generators.markdown import create_all_markdown_variants, create_markdown
from .generators.pdf import create_all_variants as create_all_pdf_variants
from .generators.pdf import create_pdf

# =============================================================================
# File Extension Mappings
# =============================================================================

_FORMAT_EXTENSIONS: dict[Format, str] = {
    Format.PDF: ".pdf",
    Format.IMAGE: ".png",
    Format.MARKDOWN: ".md",
    Format.HTML: ".html",
    Format.DOCX: ".docx",
    Format.ICS: ".ics",
    Format.EML: ".eml",
}
"""Default file extension for each format."""

_IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg"}
"""Valid image file extensions."""


# =============================================================================
# Format Dispatch Table
# =============================================================================

# Each entry maps a Format to (single_create_fn, batch_create_fn).
# Both functions share the same signature pattern.
_FORMAT_DISPATCH: dict[Format, tuple] = {
    Format.PDF: (create_pdf, create_all_pdf_variants),
    Format.IMAGE: (create_image, create_all_image_variants),
    Format.MARKDOWN: (create_markdown, create_all_markdown_variants),
    Format.HTML: (create_html, create_all_html_variants),
    Format.DOCX: (create_docx, create_all_docx_variants),
    Format.ICS: (create_ics, create_all_ics_variants),
    Format.EML: (create_eml, create_all_eml_variants),
}
"""Maps each format to its (single, batch) generator functions."""


# =============================================================================
# Result Model
# =============================================================================


@dataclass
class GenerateResult:
    """Result of a document generation operation.

    Attributes:
        campaigns: List of Campaign objects created during generation.
        skipped: Number of campaigns skipped due to duplicate UUIDs
            (deterministic seed mode).
        errors: List of error messages encountered during generation.
    """

    campaigns: list[Campaign] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# =============================================================================
# Output Path Resolution
# =============================================================================


def _resolve_output_path(
    output: Path,
    format_name: Format,
    technique: Technique,
    base_name: str,
) -> Path:
    """Resolve the output file path for single-technique generation.

    If the output path has a matching extension, uses it as-is.
    Otherwise, treats output as a directory and constructs a filename
    from the base name, technique, and format extension.

    Args:
        output: User-provided output path (file or directory).
        format_name: Document format being generated.
        technique: Technique being used (for filename construction).
        base_name: Base filename prefix.

    Returns:
        Resolved Path for the output file.

    """
    ext = _FORMAT_EXTENSIONS[format_name]

    # Image format: EXIF metadata uses .jpg
    if format_name == Format.IMAGE and technique == Technique.EXIF_METADATA:
        ext = ".jpg"

    # Check if output looks like a file path with correct extension
    if format_name == Format.IMAGE:
        is_file = output.suffix in _IMAGE_EXTENSIONS
    else:
        is_file = output.suffix == ext

    if is_file:
        output.parent.mkdir(parents=True, exist_ok=True)
        return output

    # Treat as directory
    output.mkdir(parents=True, exist_ok=True)
    return output / f"{base_name}_{technique.value}{ext}"


# =============================================================================
# Core Generation Function
# =============================================================================


def _save_campaign(campaign: Campaign, seed: int | None) -> str | None:
    """Save a campaign to the database, handling deterministic duplicates.

    Args:
        campaign: Campaign to persist.
        seed: If provided, duplicate UUIDs are expected and silently skipped.

    Returns:
        None on success, or an error message string if skipped/failed.
    """
    try:
        db.save_campaign(campaign)
    except sqlite3.IntegrityError:
        if seed is not None:
            return f"UUID {campaign.uuid[:8]}... already exists (seed={seed})"
        raise
    else:
        return None


def generate_documents(
    callback_url: str,
    output: Path,
    format_name: Format,
    techniques: list[Technique],
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    base_name: str = "report",
    seed: int | None = None,
) -> GenerateResult:
    """Generate payload documents and persist campaigns to the database.

    This is the shared generation entry point used by both the CLI and
    the web API. It handles format routing, output path resolution,
    batch vs single generation, and campaign persistence.

    Args:
        callback_url: Base URL for the callback server
            (e.g., "http://localhost:8080").
        output: Output path — file path for single technique,
            directory path for multiple techniques.
        format_name: Document format to generate.
        techniques: List of techniques to generate. Must be valid
            for the specified format (caller is responsible for
            filtering via get_techniques_for_format).
        payload_style: Social engineering style for payload content.
        payload_type: Attack objective type.
        base_name: Base filename prefix for generated documents.
        seed: Optional integer seed for deterministic generation.

    Returns:
        GenerateResult with campaigns, skip count, and any errors.

    Raises:
        ValueError: If format_name has no registered dispatch entry.
    """
    # Sanitize base_name to prevent path traversal
    base_name = Path(base_name).name
    if not base_name or base_name in {".", ".."}:
        raise ValueError("Invalid base filename")

    result = GenerateResult()

    if format_name not in _FORMAT_DISPATCH:
        raise ValueError(f"No generator registered for format: {format_name.value}")

    single_fn, batch_fn = _FORMAT_DISPATCH[format_name]

    if len(techniques) > 1:
        # Batch generation: output is a directory
        output_dir = output
        output_dir.mkdir(parents=True, exist_ok=True)

        campaigns = batch_fn(
            output_dir,
            callback_url,
            base_name,
            payload_style,
            payload_type,
            techniques,
            seed=seed,
        )

        for campaign in campaigns:
            campaign.output_path = str(output_dir / campaign.filename)
            err = _save_campaign(campaign, seed)
            if err:
                result.skipped += 1
                result.errors.append(err)
            else:
                result.campaigns.append(campaign)
    else:
        # Single technique generation
        tech = techniques[0]
        file_path = _resolve_output_path(output, format_name, tech, base_name)

        campaign = single_fn(
            file_path,
            tech,
            callback_url,
            payload_style,
            payload_type,
            seed=seed,
        )
        campaign.output_path = str(file_path)

        err = _save_campaign(campaign, seed)
        if err:
            result.skipped += 1
            result.errors.append(err)
        else:
            result.campaigns.append(campaign)

    return result
