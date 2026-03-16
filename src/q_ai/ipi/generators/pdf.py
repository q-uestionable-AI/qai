"""PDF payload generator with hidden prompt injection techniques.

This module implements all PDF-specific hiding techniques for q_ai IPI:
- Phase 1: white_ink, off_canvas, metadata
- Phase 2: tiny_text, white_rect, form_field, annotation, javascript,
           embedded_file, incremental

Usage:
    >>> from q_ai.ipi.generators.pdf import create_pdf
    >>> from q_ai.ipi.models import Technique
    >>> campaign = create_pdf(
    ...     Path("./output.pdf"),
    ...     Technique.WHITE_INK,
    ...     "http://localhost:8080"
    ... )
"""

import uuid
from pathlib import Path

from reportlab.lib.colors import black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from q_ai.ipi.models import Campaign, Format, PayloadStyle, PayloadType, Technique

from . import create_campaign_ids, generate_payload

# PDF techniques organized by phase
PDF_PHASE1_TECHNIQUES = [Technique.WHITE_INK, Technique.OFF_CANVAS, Technique.METADATA]
"""Phase 1 PDF techniques (basic hiding methods)."""

PDF_PHASE2_TECHNIQUES = [
    Technique.TINY_TEXT,
    Technique.WHITE_RECT,
    Technique.FORM_FIELD,
    Technique.ANNOTATION,
    Technique.JAVASCRIPT,
    Technique.EMBEDDED_FILE,
    Technique.INCREMENTAL,
]
"""Phase 2 PDF techniques (advanced hiding methods)."""

PDF_ALL_TECHNIQUES = PDF_PHASE1_TECHNIQUES + PDF_PHASE2_TECHNIQUES
"""All PDF techniques."""


# =============================================================================
# Phase 1 Techniques
# =============================================================================


def _inject_white_ink(c: canvas.Canvas, payload: str, page_height: float) -> None:
    """Inject payload as white text on white background.

    Args:
        c: ReportLab canvas object.
        payload: Payload string to inject.
        page_height: Page height for positioning.
    """
    c.setFillColor(white)
    c.setFont("Helvetica", 1)  # Tiny font
    c.drawString(72, page_height - 200, payload)


def _inject_off_canvas(c: canvas.Canvas, payload: str) -> None:
    """Inject payload at negative coordinates (off visible page).

    Args:
        c: ReportLab canvas object.
        payload: Payload string to inject.
    """
    c.setFillColor(black)
    c.setFont("Helvetica", 10)
    c.drawString(-1000, -1000, payload)


def _inject_metadata(c: canvas.Canvas, payload: str) -> None:
    """Inject payload into PDF metadata fields.

    Args:
        c: ReportLab canvas object.
        payload: Payload string to inject.
    """
    c.setAuthor(payload)
    c.setSubject(payload)
    c.setKeywords(payload)


# =============================================================================
# Phase 2 Techniques
# =============================================================================


def _inject_tiny_text(c: canvas.Canvas, payload: str, page_height: float) -> None:
    """Inject payload as extremely tiny text (0.5pt - below visual threshold).

    Args:
        c: ReportLab canvas object.
        payload: Payload string to inject.
        page_height: Page height for positioning.
    """
    c.setFillColor(black)
    c.setFont("Helvetica", 0.5)  # Half-point font - invisible to humans
    c.drawString(72, page_height - 200, payload)


def _inject_white_rect(c: canvas.Canvas, payload: str, page_height: float) -> None:
    """Inject payload as text covered by a white rectangle.

    Args:
        c: ReportLab canvas object.
        payload: Payload string to inject.
        page_height: Page height for positioning.
    """
    # First draw the payload text in black
    c.setFillColor(black)
    c.setFont("Helvetica", 10)
    x, y = 72, page_height - 200
    c.drawString(x, y, payload)

    # Then cover it with a white rectangle
    text_width = len(payload) * 6
    text_height = 14
    c.setFillColor(white)
    c.rect(x - 2, y - 4, text_width + 4, text_height, fill=1, stroke=0)


def _inject_form_field(c: canvas.Canvas, payload: str, page_height: float) -> None:
    """Inject payload into a hidden AcroForm text field.

    Args:
        c: ReportLab canvas object.
        payload: Payload string to inject.
        page_height: Page height for positioning (unused, field is off-screen).
    """
    form = c.acroForm
    form.textfield(
        name="hidden_data",
        x=-1000,
        y=-1000,
        width=1,
        height=1,
        value=payload,
        textColor=white,
        fillColor=white,
        borderWidth=0,
    )


def _inject_annotation(c: canvas.Canvas, payload: str, page_height: float, target_url: str) -> None:
    """Inject payload as a PDF link annotation with callback URL as URI.

    Args:
        c: ReportLab canvas object.
        payload: Full payload text (unused, URL is the payload carrier).
        page_height: Page height for positioning.
        target_url: The callback URL to use as the link URI.
    """
    c.linkURL(
        target_url,
        (-100, -100, -90, -90),  # Off-page rectangle
        relative=0,
    )


def _add_javascript(output_path: Path, payload: str) -> None:
    """Add JavaScript to PDF using pypdf.

    Args:
        output_path: Path to the PDF file.
        payload: Payload string to embed in JavaScript.
    """
    import pypdf

    reader = pypdf.PdfReader(str(output_path))
    writer = pypdf.PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    escaped_payload = payload.replace("\\", "\\\\").replace('"', '\\"')
    js_code = f'var hiddenData = "{escaped_payload}";'
    writer.add_js(js_code)

    with output_path.open("wb") as f:
        writer.write(f)


def _add_embedded_file(output_path: Path, payload: str) -> None:
    """Add payload as embedded file attachment using pypdf.

    Args:
        output_path: Path to the PDF file.
        payload: Payload string to embed as file content.
    """
    import pypdf

    reader = pypdf.PdfReader(str(output_path))
    writer = pypdf.PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    payload_bytes = payload.encode("utf-8")
    writer.add_attachment("data.txt", payload_bytes)

    with output_path.open("wb") as f:
        writer.write(f)


def _add_incremental_update(output_path: Path, payload: str) -> None:
    """Add payload via incremental update (appends to PDF without rewriting).

    Args:
        output_path: Path to the PDF file.
        payload: Payload string to add as metadata.
    """
    import pypdf

    reader = pypdf.PdfReader(str(output_path))
    writer = pypdf.PdfWriter()

    writer.clone_document_from_reader(reader)
    writer.add_metadata(
        {
            "/Hidden": payload,
            "/UpdateNote": payload,
        }
    )

    with output_path.open("ab") as f:  # Append mode for incremental
        writer.write(f)


# =============================================================================
# Main PDF Creation
# =============================================================================


def create_pdf(
    output_path: Path,
    technique: Technique,
    callback_url: str,
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    decoy_title: str = "Quarterly Financial Report - Q1 2026",
    decoy_content: str = "This document contains confidential business information.",
    seed: int | None = None,
    sequence: int = 0,
) -> Campaign:
    """Generate a PDF with hidden prompt injection payload.

    Args:
        output_path: Where to save the PDF.
        technique: Hiding technique to use (must be a PDF technique).
        callback_url: Base URL for callbacks.
        payload_style: Style of payload content (obvious vs subtle).
        payload_type: Objective of the payload.
        decoy_title: Visible title text.
        decoy_content: Visible body text.
        seed: Optional seed for deterministic UUID/token generation.
        sequence: Sequence number for batch deterministic generation.

    Returns:
        Campaign object with UUID and metadata.

    Raises:
        ValueError: If technique is not a PDF technique.
    """
    canary_uuid, token = create_campaign_ids(seed, sequence)
    payload = generate_payload(callback_url, canary_uuid, payload_style, payload_type, token=token)

    base_url = callback_url if callback_url.endswith("/") else callback_url + "/"
    target_url = f"{base_url}c/{canary_uuid}/{token}"

    c = canvas.Canvas(str(output_path), pagesize=letter)
    _width, height = letter

    # Draw visible decoy content
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 72, decoy_title)

    c.setFont("Helvetica", 12)
    c.drawString(72, height - 100, decoy_content)
    c.drawString(72, height - 120, "Please summarize the key findings below.")

    # Inject payload using selected technique
    if technique == Technique.WHITE_INK:
        _inject_white_ink(c, payload, height)
    elif technique == Technique.OFF_CANVAS:
        _inject_off_canvas(c, payload)
    elif technique == Technique.METADATA:
        _inject_metadata(c, payload)
    elif technique == Technique.TINY_TEXT:
        _inject_tiny_text(c, payload, height)
    elif technique == Technique.WHITE_RECT:
        _inject_white_rect(c, payload, height)
    elif technique == Technique.FORM_FIELD:
        _inject_form_field(c, payload, height)
    elif technique == Technique.ANNOTATION:
        _inject_annotation(c, payload, height, target_url)
    elif technique == Technique.JAVASCRIPT:
        c.save()
        _add_javascript(output_path, payload)
        return Campaign(
            id=uuid.uuid4().hex,
            uuid=canary_uuid,
            token=token,
            filename=output_path.name,
            format=Format.PDF,
            technique=technique,
            payload_style=payload_style,
            payload_type=payload_type,
            callback_url=callback_url,
        )
    elif technique == Technique.EMBEDDED_FILE:
        c.save()
        _add_embedded_file(output_path, payload)
        return Campaign(
            id=uuid.uuid4().hex,
            uuid=canary_uuid,
            token=token,
            filename=output_path.name,
            format=Format.PDF,
            technique=technique,
            payload_style=payload_style,
            payload_type=payload_type,
            callback_url=callback_url,
        )
    elif technique == Technique.INCREMENTAL:
        c.save()
        _add_incremental_update(output_path, payload)
        return Campaign(
            id=uuid.uuid4().hex,
            uuid=canary_uuid,
            token=token,
            filename=output_path.name,
            format=Format.PDF,
            technique=technique,
            payload_style=payload_style,
            payload_type=payload_type,
            callback_url=callback_url,
        )
    else:
        raise ValueError(f"Unsupported PDF technique: {technique.value}")

    c.save()

    return Campaign(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        token=token,
        filename=output_path.name,
        format=Format.PDF,
        technique=technique,
        payload_style=payload_style,
        payload_type=payload_type,
        callback_url=callback_url,
    )


# =============================================================================
# Batch Generation
# =============================================================================


def create_all_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "report",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    techniques: list[Technique] | None = None,
    seed: int | None = None,
) -> list[Campaign]:
    """Generate PDFs using multiple techniques.

    Args:
        output_dir: Directory to save PDFs.
        callback_url: Base URL for callbacks.
        base_name: Base filename (technique suffix will be added).
        payload_style: Style of payload content.
        payload_type: Objective of the payload.
        techniques: List of techniques to use (default: all PDF techniques).
        seed: Optional seed for deterministic UUID/token generation.

    Returns:
        List of Campaign objects.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    campaigns = []

    if techniques is None:
        techniques = PDF_ALL_TECHNIQUES

    for i, technique in enumerate(techniques):
        filename = f"{base_name}_{technique.value}.pdf"
        output_path = output_dir / filename
        campaign = create_pdf(
            output_path,
            technique,
            callback_url,
            payload_style,
            payload_type,
            seed=seed,
            sequence=i,
        )
        campaigns.append(campaign)

    return campaigns


def create_phase1_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "report",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
) -> list[Campaign]:
    """Generate PDFs using only Phase 1 techniques.

    Args:
        output_dir: Directory to save PDFs.
        callback_url: Base URL for callbacks.
        base_name: Base filename.
        payload_style: Style of payload content.
        payload_type: Objective of the payload.

    Returns:
        List of Campaign objects.
    """
    return create_all_variants(
        output_dir, callback_url, base_name, payload_style, payload_type, PDF_PHASE1_TECHNIQUES
    )


def create_phase2_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "report",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
) -> list[Campaign]:
    """Generate PDFs using only Phase 2 techniques.

    Args:
        output_dir: Directory to save PDFs.
        callback_url: Base URL for callbacks.
        base_name: Base filename.
        payload_style: Style of payload content.
        payload_type: Objective of the payload.

    Returns:
        List of Campaign objects.
    """
    return create_all_variants(
        output_dir, callback_url, base_name, payload_style, payload_type, PDF_PHASE2_TECHNIQUES
    )
