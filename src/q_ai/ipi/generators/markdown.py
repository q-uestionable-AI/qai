"""Markdown payload generator for RAG and document processing pipelines.

This module implements Markdown-based hiding techniques targeting systems
that ingest markdown documentation, README files, or convert markdown
to other formats.

Techniques:
    HTML_COMMENT: Payload hidden in HTML comment tags.
    LINK_REFERENCE: Payload in link reference definition (not rendered).
    ZERO_WIDTH: Payload encoded using zero-width Unicode characters.
    HIDDEN_BLOCK: Payload in HTML block with display:none style.

Targets:
    RAG pipelines, documentation processors, GitHub/GitLab markdown rendering,
    static site generators, markdown-to-HTML converters.

Usage:
    >>> from q_ai.ipi.generators.markdown import create_markdown
    >>> from q_ai.ipi.models import Technique
    >>> campaign = create_markdown(
    ...     Path("./output.md"),
    ...     Technique.HTML_COMMENT,
    ...     "http://localhost:8080"
    ... )
"""

import uuid
from pathlib import Path

from q_ai.ipi.models import Campaign, Format, PayloadStyle, PayloadType, Technique

from . import create_campaign_ids, generate_payload

# Markdown techniques
MARKDOWN_TECHNIQUES = [
    Technique.HTML_COMMENT,
    Technique.LINK_REFERENCE,
    Technique.ZERO_WIDTH,
    Technique.HIDDEN_BLOCK,
]
"""All Markdown techniques for document processing pipelines."""


# =============================================================================
# Zero-Width Character Encoding
# =============================================================================

# Zero-width characters for encoding
ZERO_WIDTH_SPACE = "\u200b"  # Binary 0
ZERO_WIDTH_NON_JOINER = "\u200c"  # Binary 1
ZERO_WIDTH_JOINER = "\u200d"  # Separator


def _encode_zero_width(text: str) -> str:
    """Encode text using zero-width Unicode characters.

    Each character is converted to its binary representation, then
    encoded using zero-width space (0) and zero-width non-joiner (1).
    Characters are separated by zero-width joiner.

    Args:
        text: Plain text to encode.

    Returns:
        Zero-width encoded string (invisible when rendered).
    """
    encoded_chars = []
    for char in text:
        # Convert character to 8-bit binary
        binary = format(ord(char), "08b")
        # Replace 0/1 with zero-width characters
        encoded = binary.replace("0", ZERO_WIDTH_SPACE).replace("1", ZERO_WIDTH_NON_JOINER)
        encoded_chars.append(encoded)
    return ZERO_WIDTH_JOINER.join(encoded_chars)


def _decode_zero_width(encoded: str) -> str:
    """Decode zero-width encoded text back to plain text.

    Args:
        encoded: Zero-width encoded string.

    Returns:
        Decoded plain text.
    """
    if not encoded:
        return ""

    chars = []
    for char_block in encoded.split(ZERO_WIDTH_JOINER):
        if not char_block:
            continue
        # Convert zero-width chars back to binary
        binary = char_block.replace(ZERO_WIDTH_SPACE, "0").replace(ZERO_WIDTH_NON_JOINER, "1")
        if binary and len(binary) == 8:
            chars.append(chr(int(binary, 2)))
    return "".join(chars)


# =============================================================================
# Technique Implementations
# =============================================================================


def _create_decoy_content(title: str = "Project Documentation") -> str:
    """Create plausible markdown decoy content.

    Args:
        title: Document title.

    Returns:
        Markdown string with realistic content.
    """
    return f"""# {title}

## Overview

This document provides an overview of the project architecture and key components.

## Getting Started

To get started with the project, follow these steps:

1. Clone the repository
2. Install dependencies
3. Run the setup script

## Architecture

The system consists of three main components:

- **Frontend**: React-based user interface
- **Backend**: Python FastAPI server
- **Database**: PostgreSQL for data persistence

## Configuration

Configuration is managed through environment variables. See `.env.example` for available options.

## Contributing

Please read our contributing guidelines before submitting pull requests.
"""


def _inject_html_comment(content: str, payload: str) -> str:
    """Inject payload as HTML comment in markdown.

    HTML comments are valid in markdown and are typically stripped
    during rendering but may be processed by text extractors.

    Args:
        content: Base markdown content.
        payload: Payload string to inject.

    Returns:
        Markdown with HTML comment containing payload.
    """
    comment = f"<!-- {payload} -->"
    # Insert after first heading
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            lines.insert(i + 1, comment)
            break
    return "\n".join(lines)


def _inject_link_reference(content: str, payload: str) -> str:
    """Inject payload as link reference definition.

    Link reference definitions are not rendered if not used in the document.
    Format: [ref-name]: url "title"

    Args:
        content: Base markdown content.
        payload: Payload string to inject.

    Returns:
        Markdown with link reference containing payload.
    """
    # Create an unused link reference at the end
    ref = f'\n\n[_hidden_ref]: # "{payload}"\n'
    return content + ref


def _inject_zero_width(content: str, payload: str) -> str:
    """Inject payload encoded with zero-width characters.

    The payload is encoded using invisible Unicode characters and
    inserted into the document. Completely invisible when rendered.

    Args:
        content: Base markdown content.
        payload: Payload string to inject.

    Returns:
        Markdown with zero-width encoded payload.
    """
    encoded = _encode_zero_width(payload)
    # Insert after first paragraph
    lines = content.split("\n\n")
    if len(lines) > 1:
        lines[1] = lines[1] + encoded
    else:
        lines[0] = lines[0] + encoded
    return "\n\n".join(lines)


def _inject_hidden_block(content: str, payload: str) -> str:
    """Inject payload in hidden HTML block.

    Uses an HTML div with display:none style. Not visible when rendered
    in browsers but extracted by text parsers.

    Args:
        content: Base markdown content.
        payload: Payload string to inject.

    Returns:
        Markdown with hidden HTML block containing payload.
    """
    hidden_div = f'\n\n<div style="display:none">{payload}</div>\n'
    # Insert at end of document
    return content + hidden_div


# =============================================================================
# Main Markdown Creation
# =============================================================================


def create_markdown(
    output_path: Path,
    technique: Technique,
    callback_url: str,
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    decoy_title: str = "Project Documentation",
    seed: int | None = None,
    sequence: int = 0,
) -> Campaign:
    """Generate a Markdown file with hidden prompt injection payload.

    Creates a plausible documentation file and injects the payload using
    the specified technique.

    Args:
        output_path: Where to save the markdown file.
        technique: Hiding technique (HTML_COMMENT, LINK_REFERENCE, ZERO_WIDTH, HIDDEN_BLOCK).
        callback_url: Base URL for callbacks.
        payload_style: Style of payload content (obvious vs subtle).
        payload_type: Objective of the payload.
        decoy_title: Title for the decoy document.

        seed: Optional seed for deterministic UUID/token generation.
        sequence: Sequence number for batch deterministic generation.

    Returns:
        Campaign object with UUID and metadata.

    Raises:
        ValueError: If technique is not a markdown technique.

    Example:
        >>> from q_ai.ipi.generators.markdown import create_markdown
        >>> from q_ai.ipi.models import Technique
        >>> campaign = create_markdown(
        ...     Path("./README.md"),
        ...     Technique.HTML_COMMENT,
        ...     "http://localhost:8080"
        ... )
    """
    if technique not in MARKDOWN_TECHNIQUES:
        raise ValueError(f"Unsupported markdown technique: {technique.value}")

    canary_uuid, token = create_campaign_ids(seed, sequence)
    payload = generate_payload(callback_url, canary_uuid, payload_style, payload_type, token=token)

    # Create base content
    content = _create_decoy_content(decoy_title)

    # Inject payload using selected technique
    if technique == Technique.HTML_COMMENT:
        content = _inject_html_comment(content, payload)
    elif technique == Technique.LINK_REFERENCE:
        content = _inject_link_reference(content, payload)
    elif technique == Technique.ZERO_WIDTH:
        content = _inject_zero_width(content, payload)
    elif technique == Technique.HIDDEN_BLOCK:
        content = _inject_hidden_block(content, payload)

    # Write file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return Campaign(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        token=token,
        filename=output_path.name,
        format=Format.MARKDOWN,
        technique=technique,
        payload_style=payload_style,
        payload_type=payload_type,
        callback_url=callback_url,
    )


# =============================================================================
# Batch Generation
# =============================================================================


def create_all_markdown_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "document",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    techniques: list[Technique] | None = None,
    seed: int | None = None,
) -> list[Campaign]:
    """Generate markdown files using multiple techniques.

    Args:
        output_dir: Directory to save files.
        callback_url: Base URL for callbacks.
        base_name: Base filename (technique suffix will be added).
        payload_style: Style of payload content.
        payload_type: Objective of the payload.
        techniques: List of techniques to use (default: all markdown techniques).

        seed: Optional seed for deterministic UUID/token generation.

    Returns:
        List of Campaign objects.

    Example:
        >>> from q_ai.ipi.generators.markdown import create_all_markdown_variants
        >>> campaigns = create_all_markdown_variants(
        ...     Path("./output"),
        ...     "http://localhost:8080"
        ... )
        >>> len(campaigns)
        4
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    campaigns = []

    if techniques is None:
        techniques = MARKDOWN_TECHNIQUES

    for i, technique in enumerate(techniques):
        filename = f"{base_name}_{technique.value}.md"
        output_path = output_dir / filename
        campaign = create_markdown(
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
