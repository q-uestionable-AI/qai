"""Image payload generator for VLM attack surface testing.

This module implements image-based hiding techniques targeting Vision Language
Models (VLMs) that process screenshots, receipts, documents, and user-uploaded
images.

Techniques:
    VISIBLE_TEXT: Human-readable text overlay on image.
    SUBTLE_TEXT: Low contrast, small font, or edge-placed text.
    EXIF_METADATA: Payload embedded in EXIF metadata fields.

Targets:
    GPT-4V, Claude Vision, LLaVA, Gemini, medical imaging AI,
    receipt processors, screenshot analyzers.

Usage:
    >>> from q_ai.ipi.generators.image import create_image
    >>> from q_ai.ipi.models import Technique
    >>> campaign = create_image(
    ...     Path("./output.png"),
    ...     Technique.VISIBLE_TEXT,
    ...     "http://localhost:8080"
    ... )
"""

import uuid
from pathlib import Path

import piexif
from PIL import Image, ImageDraw, ImageFont

from q_ai.ipi.models import Campaign, Format, PayloadStyle, PayloadType, Technique

from . import create_campaign_ids, generate_payload

# Image techniques
IMAGE_TECHNIQUES = [
    Technique.VISIBLE_TEXT,
    Technique.SUBTLE_TEXT,
    Technique.EXIF_METADATA,
]
"""All image techniques for VLM attack surface."""


# =============================================================================
# Technique Implementations
# =============================================================================


def _create_base_image(
    size: tuple[int, int],
    decoy_title: str,
) -> Image.Image:
    """Create a base image with decoy content resembling a receipt/document.

    Args:
        size: Width and height in pixels.
        decoy_title: Title text to display on the image.

    Returns:
        PIL Image object with decoy content.
    """
    img = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(img)
    width, _height = img.size

    # Try to use a nice font, fall back to default
    try:
        title_font = ImageFont.truetype("arial.ttf", 24)  # type: ignore[assignment]
        body_font = ImageFont.truetype("arial.ttf", 14)  # type: ignore[assignment]
    except OSError:
        title_font = ImageFont.load_default()  # type: ignore[assignment]
        body_font = ImageFont.load_default()  # type: ignore[assignment]

    # Draw decoy content - receipt-like appearance
    y_pos = 40
    draw.text((width // 2 - 80, y_pos), decoy_title, fill="black", font=title_font)
    y_pos += 50

    draw.line([(50, y_pos), (width - 50, y_pos)], fill="gray", width=1)
    y_pos += 20

    # Fake receipt items
    items = [
        ("Item 1", "$12.99"),
        ("Item 2", "$8.50"),
        ("Item 3", "$15.00"),
        ("Subtotal", "$36.49"),
        ("Tax", "$2.92"),
        ("Total", "$39.41"),
    ]

    for item, price in items:
        draw.text((60, y_pos), item, fill="black", font=body_font)
        draw.text((width - 120, y_pos), price, fill="black", font=body_font)
        y_pos += 25

    draw.line([(50, y_pos), (width - 50, y_pos)], fill="gray", width=1)
    y_pos += 30

    draw.text((width // 2 - 60, y_pos), "Thank you!", fill="gray", font=body_font)

    return img


def _inject_visible_text(img: Image.Image, payload: str) -> None:
    """Inject payload as clearly visible text overlay.

    This technique tests whether VLMs will follow instructions that are
    plainly visible to humans. Places text at the bottom of the image
    with a background for readability. Text is wrapped to fit within
    the image width.

    Args:
        img: PIL Image to modify in place.
        payload: Payload string to inject.
    """
    import textwrap

    draw = ImageDraw.Draw(img)
    width, height = img.size

    try:
        font = ImageFont.truetype("arial.ttf", 16)  # type: ignore[assignment]
    except OSError:
        font = ImageFont.load_default()  # type: ignore[assignment]

    # Calculate max characters per line based on image width
    padding = 10
    max_width = width - (padding * 4)  # Leave margins on both sides

    # Estimate chars per line (approx 8px per char at 16pt)
    avg_char_width = 8
    chars_per_line = max(20, max_width // avg_char_width)

    # Wrap text to fit
    wrapped_lines = textwrap.wrap(payload, width=chars_per_line)
    wrapped_text = "\n".join(wrapped_lines)

    # Calculate text dimensions for wrapped text
    text_bbox = draw.textbbox((0, 0), wrapped_text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]

    x = padding
    y = height - text_height - padding * 2 - 10

    # Draw background rectangle
    draw.rectangle(
        (x - padding, y - padding, x + text_width + padding * 2, y + text_height + padding),
        fill="white",
        outline="gray",
    )
    draw.text((x, y), wrapped_text, fill="black", font=font)


def _inject_subtle_text(img: Image.Image, payload: str) -> None:
    """Inject payload as subtle, hard-to-notice text.

    Uses low contrast (light gray on white), small font size, and edge
    placement to make the text nearly invisible to casual human review
    but potentially readable by VLMs.

    Args:
        img: PIL Image to modify in place.
        payload: Payload string to inject.
    """
    draw = ImageDraw.Draw(img)
    width, height = img.size

    try:
        # Very small font - 6pt equivalent
        font = ImageFont.truetype("arial.ttf", 8)  # type: ignore[assignment]
    except OSError:
        font = ImageFont.load_default()  # type: ignore[assignment]

    # Light gray on white background - low contrast
    subtle_color = (220, 220, 220)

    # Place at bottom right corner, truncate if needed
    max_chars = 80
    text = payload[:max_chars] if len(payload) > max_chars else payload

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]

    x = width - text_width - 5
    y = height - 12

    draw.text((x, y), text, fill=subtle_color, font=font)


def _inject_exif_metadata(output_path: Path, payload: str) -> None:
    """Inject payload into EXIF metadata fields.

    Embeds the payload in multiple EXIF fields (ImageDescription, UserComment,
    Artist) that some VLM pipelines may read when processing images.

    Args:
        output_path: Path to the saved image file.
        payload: Payload string to inject.
    """
    # Load the existing image
    img = Image.open(output_path)

    # Encode payload as bytes for EXIF
    payload_bytes = payload.encode("utf-8")

    # Create EXIF data with payload in multiple fields
    exif_dict = {
        "0th": {
            piexif.ImageIFD.ImageDescription: payload_bytes,
            piexif.ImageIFD.Artist: payload_bytes,
            piexif.ImageIFD.Copyright: payload_bytes,
            piexif.ImageIFD.Software: b"q_ai",
        },
        "Exif": {
            # UserComment requires specific encoding format: charset code (8 bytes) + content
            # Using ASCII charset code
            piexif.ExifIFD.UserComment: b"ASCII\x00\x00\x00" + payload_bytes,
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }

    exif_bytes = piexif.dump(exif_dict)

    # Save with EXIF data
    img.save(output_path, exif=exif_bytes)


# =============================================================================
# Main Image Creation
# =============================================================================


def create_image(
    output_path: Path,
    technique: Technique,
    callback_url: str,
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    decoy_title: str = "Receipt",
    image_size: tuple[int, int] = (800, 600),
    seed: int | None = None,
    sequence: int = 0,
    encoding: str = "none",
    top_instruction: str = "",
    context_template: str = "",
) -> Campaign:
    """Generate an image with hidden prompt injection payload.

    Creates a receipt-like decoy image and injects the payload using the
    specified technique. Supports PNG and JPG output formats.

    Args:
        output_path: Where to save the image (PNG or JPG).
        technique: Hiding technique (VISIBLE_TEXT, SUBTLE_TEXT, or EXIF_METADATA).
        callback_url: Base URL for callbacks.
        payload_style: Style of payload content (obvious vs subtle).
        payload_type: Objective of the payload.
        decoy_title: Visible title on the decoy image.
        image_size: Width and height in pixels.

        seed: Optional seed for deterministic UUID/token generation.
        sequence: Sequence number for batch deterministic generation.

    Returns:
        Campaign object with UUID and metadata.

    Raises:
        ValueError: If technique is not an image technique.

    Example:
        >>> from q_ai.ipi.generators.image import create_image
        >>> from q_ai.ipi.models import Technique
        >>> campaign = create_image(
        ...     Path("./receipt.png"),
        ...     Technique.VISIBLE_TEXT,
        ...     "http://localhost:8080"
        ... )
    """
    if technique not in IMAGE_TECHNIQUES:
        raise ValueError(f"Unsupported image technique: {technique.value}")

    # Image format is not covered by any non-GENERIC document template,
    # so template framing parameters are accepted for signature uniformity
    # but currently have no visual representation.
    del top_instruction, context_template

    canary_uuid, token = create_campaign_ids(seed, sequence)
    payload = generate_payload(
        callback_url,
        canary_uuid,
        payload_style,
        payload_type,
        token=token,
        encoding=encoding,
    )

    # Create base image with decoy content
    img = _create_base_image(image_size, decoy_title)

    # Inject payload using selected technique
    if technique == Technique.VISIBLE_TEXT:
        _inject_visible_text(img, payload)
        img.save(output_path)

    elif technique == Technique.SUBTLE_TEXT:
        _inject_subtle_text(img, payload)
        img.save(output_path)

    elif technique == Technique.EXIF_METADATA:
        # Save first, then add EXIF (EXIF requires JPEG format ideally)
        # Force JPEG for EXIF support
        if output_path.suffix.lower() == ".png":
            output_path = output_path.with_suffix(".jpg")
        img.save(output_path, "JPEG", quality=95)
        _inject_exif_metadata(output_path, payload)

    return Campaign(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        token=token,
        filename=output_path.name,
        format=Format.IMAGE,
        technique=technique,
        payload_style=payload_style,
        payload_type=payload_type,
        callback_url=callback_url,
    )


# =============================================================================
# Batch Generation
# =============================================================================


def create_all_image_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "image",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    techniques: list[Technique] | None = None,
    seed: int | None = None,
    encoding: str = "none",
    top_instruction: str = "",
    context_template: str = "",
) -> list[Campaign]:
    """Generate images using multiple techniques.

    Args:
        output_dir: Directory to save images.
        callback_url: Base URL for callbacks.
        base_name: Base filename (technique suffix will be added).
        payload_style: Style of payload content.
        payload_type: Objective of the payload.
        techniques: List of techniques to use (default: all image techniques).

        seed: Optional seed for deterministic UUID/token generation.

    Returns:
        List of Campaign objects.

    Example:
        >>> from q_ai.ipi.generators.image import create_all_image_variants
        >>> campaigns = create_all_image_variants(
        ...     Path("./output"),
        ...     "http://localhost:8080"
        ... )
        >>> len(campaigns)
        3
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    campaigns = []

    if techniques is None:
        techniques = IMAGE_TECHNIQUES

    for i, technique in enumerate(techniques):
        # Use .jpg for EXIF, .png for others
        ext = ".jpg" if technique == Technique.EXIF_METADATA else ".png"
        filename = f"{base_name}_{technique.value}{ext}"
        output_path = output_dir / filename
        campaign = create_image(
            output_path,
            technique,
            callback_url,
            payload_style,
            payload_type,
            seed=seed,
            sequence=i,
            encoding=encoding,
            top_instruction=top_instruction,
            context_template=context_template,
        )
        campaigns.append(campaign)

    return campaigns
