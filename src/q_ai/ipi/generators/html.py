"""HTML payload generator for web and document processing pipelines.

This module implements HTML-based hiding techniques targeting systems
that ingest HTML documents, web pages, or process HTML content.

Techniques:
    SCRIPT_COMMENT: Payload hidden in JavaScript comment inside script tag.
    CSS_OFFSCREEN: Payload in element positioned off-screen with CSS.
    DATA_ATTRIBUTE: Payload in HTML data-* attribute on element.
    META_TAG: Payload in HTML meta tag content.

Targets:
    Web scrapers, HTML-to-text converters, RAG pipelines ingesting web content,
    document processors, email HTML parsers.

Usage:
    >>> from q_ai.ipi.generators.html import create_html
    >>> from q_ai.ipi.models import Technique
    >>> campaign = create_html(
    ...     Path("./output.html"),
    ...     Technique.SCRIPT_COMMENT,
    ...     "http://localhost:8080"
    ... )
"""

import uuid
from html import escape as _html_escape
from pathlib import Path

from q_ai.ipi.models import Campaign, DocumentTemplate, Format, PayloadStyle, PayloadType, Technique

from . import create_campaign_ids, generate_payload

# HTML techniques
HTML_TECHNIQUES = [
    Technique.SCRIPT_COMMENT,
    Technique.CSS_OFFSCREEN,
    Technique.DATA_ATTRIBUTE,
    Technique.META_TAG,
]
"""All HTML techniques for web/document processing pipelines."""


# =============================================================================
# Decoy Content
# =============================================================================


def _create_decoy_content(title: str = "Company News") -> str:
    """Create plausible HTML decoy content.

    Args:
        title: Page title.

    Returns:
        HTML string with realistic content structure.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
        h1 {{ color: #333; }}
        .content {{ max-width: 800px; margin: 0 auto; }}
        .article {{ margin-bottom: 20px; padding: 15px; background: #f9f9f9; }}
    </style>
</head>
<body>
    <div class="content">
        <h1>{title}</h1>

        <div class="article">
            <h2>Q4 Results Exceed Expectations</h2>
            <p>We are pleased to announce that our fourth quarter results have exceeded
            analyst expectations by 15%. Revenue grew to $2.3 billion, driven by strong
            performance in our cloud services division.</p>
        </div>

        <div class="article">
            <h2>New Product Launch</h2>
            <p>Our engineering team has been working on an exciting new product that
            will revolutionize how businesses manage their workflows. Stay tuned for
            the official announcement next month.</p>
        </div>

        <div class="article">
            <h2>Team Updates</h2>
            <p>We welcome Sarah Johnson as our new VP of Engineering. Sarah brings
            over 20 years of experience in building scalable systems at leading
            technology companies.</p>
        </div>
    </div>
</body>
</html>"""


# =============================================================================
# Technique Implementations
# =============================================================================


def _inject_script_comment(content: str, payload: str) -> str:
    """Inject payload as JavaScript comment in script tag.

    JavaScript comments are typically stripped during rendering but may
    be extracted by text processors that don't execute JS.

    Args:
        content: Base HTML content.
        payload: Payload string to inject.

    Returns:
        HTML with script tag containing commented payload.
    """
    script_block = f"""
    <script>
    // Application configuration
    /* {payload} */
    var config = {{ version: "1.0.0" }};
    </script>"""

    # Insert before closing </body>
    return content.replace("</body>", f"{script_block}\n</body>")


def _inject_css_offscreen(content: str, payload: str) -> str:
    """Inject payload in element positioned off-screen with CSS.

    Uses absolute positioning to place element far outside the viewport.
    Invisible to users but extractable by parsers.

    Args:
        content: Base HTML content.
        payload: Payload string to inject.

    Returns:
        HTML with off-screen element containing payload.
    """
    offscreen_div = (
        f'    <div style="position: absolute; left: -9999px; top: -9999px;'
        f' width: 1px; height: 1px; overflow: hidden;">\n'
        f"        {payload}\n"
        f"    </div>"
    )

    # Insert before closing </body>
    return content.replace("</body>", f"{offscreen_div}\n</body>")


def _inject_data_attribute(content: str, payload: str) -> str:
    """Inject payload in HTML data-* attribute.

    Data attributes are valid HTML5 and often extracted by scrapers
    looking for structured data.

    Args:
        content: Base HTML content.
        payload: Payload string to inject.

    Returns:
        HTML with data attribute containing payload.
    """
    # Add data attribute to the body tag
    return content.replace("<body>", f'<body data-config="{payload}">')


def _inject_none(content: str, payload: str) -> str:
    """Render payload as a normal visible paragraph (control condition).

    No hiding is applied: the payload is inserted as an ordinary ``<p>``
    element. Used as a baseline for measuring hiding technique uplift.
    Payload text is HTML-escaped to preserve document structure.

    Args:
        content: Base HTML content.
        payload: Payload string to render.

    Returns:
        HTML with payload as a visible paragraph before ``</body>``.
    """
    paragraph = f"    <p>{_html_escape(payload)}</p>"
    return content.replace("</body>", f"{paragraph}\n</body>")


def _inject_meta_tag(content: str, payload: str) -> str:
    """Inject payload in HTML meta tag.

    Meta tags are commonly extracted for SEO analysis, indexing,
    and document processing.

    Args:
        content: Base HTML content.
        payload: Payload string to inject.

    Returns:
        HTML with meta tag containing payload.
    """
    meta_tag = f'    <meta name="generator" content="{payload}">'

    # Insert after <title> tag
    return content.replace("</title>", f"</title>\n{meta_tag}")


# =============================================================================
# Main HTML Creation
# =============================================================================


def create_html(
    output_path: Path,
    technique: Technique,
    callback_url: str,
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    decoy_title: str = "Company News",
    seed: int | None = None,
    sequence: int = 0,
    encoding: str = "none",
    top_instruction: str = "",
    context_template: str = "",
    template: DocumentTemplate = DocumentTemplate.GENERIC,
) -> Campaign:
    """Generate an HTML file with hidden prompt injection payload.

    Creates a plausible web page and injects the payload using
    the specified technique.

    Args:
        output_path: Where to save the HTML file.
        technique: Hiding technique (SCRIPT_COMMENT, CSS_OFFSCREEN, DATA_ATTRIBUTE, META_TAG).
        callback_url: Base URL for callbacks.
        payload_style: Style of payload content (obvious vs subtle).
        payload_type: Objective of the payload.
        decoy_title: Title for the decoy page.

        seed: Optional seed for deterministic UUID/token generation.
        sequence: Sequence number for batch deterministic generation.
        encoding: Callback URL encoding ("none", "base16", "hex").
        template: Document context template forwarded to
            :func:`generate_payload` so non-OBVIOUS CALLBACK style bodies
            can interpolate the template's ``callback_role``. ``GENERIC``
            (default) preserves legacy behavior for OBVIOUS and
            non-CALLBACK payloads.

    Returns:
        Campaign object with UUID and metadata.

    Raises:
        ValueError: If technique is not an HTML technique.

    Example:
        >>> from q_ai.ipi.generators.html import create_html
        >>> from q_ai.ipi.models import Technique
        >>> campaign = create_html(
        ...     Path("./page.html"),
        ...     Technique.SCRIPT_COMMENT,
        ...     "http://localhost:8080"
        ... )
    """
    if technique not in HTML_TECHNIQUES and technique is not Technique.NONE:
        raise ValueError(f"Unsupported HTML technique: {technique.value}")

    canary_uuid, token = create_campaign_ids(seed, sequence)
    payload = generate_payload(
        callback_url,
        canary_uuid,
        payload_style,
        payload_type,
        token=token,
        encoding=encoding,
        template=template,
    )

    # Create base content
    content = _create_decoy_content(decoy_title)

    if top_instruction or context_template:
        # Escape framing text: stubs are safe today but payload strings and
        # future Phase 4.3 template content may contain <, &, or closing tags
        # that would otherwise corrupt the surrounding HTML structure.
        framing_html = ""
        if top_instruction:
            framing_html += f"<p>{_html_escape(top_instruction)}</p>\n"
        if context_template:
            rendered = context_template.replace("{payload}", payload)
            framing_html += f"<pre>{_html_escape(rendered)}</pre>\n"
        content = content.replace("<body>", f"<body>\n{framing_html}", 1)

    # Inject payload using selected technique
    if technique == Technique.SCRIPT_COMMENT:
        content = _inject_script_comment(content, payload)
    elif technique == Technique.CSS_OFFSCREEN:
        content = _inject_css_offscreen(content, payload)
    elif technique == Technique.DATA_ATTRIBUTE:
        content = _inject_data_attribute(content, payload)
    elif technique == Technique.META_TAG:
        content = _inject_meta_tag(content, payload)
    elif technique == Technique.NONE:
        content = _inject_none(content, payload)

    # Write file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return Campaign(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        token=token,
        filename=output_path.name,
        format=Format.HTML,
        technique=technique,
        payload_style=payload_style,
        payload_type=payload_type,
        callback_url=callback_url,
    )


# =============================================================================
# Batch Generation
# =============================================================================


def create_all_html_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "page",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    techniques: list[Technique] | None = None,
    seed: int | None = None,
    encoding: str = "none",
    top_instruction: str = "",
    context_template: str = "",
    template: DocumentTemplate = DocumentTemplate.GENERIC,
) -> list[Campaign]:
    """Generate HTML files using multiple techniques.

    Args:
        output_dir: Directory to save files.
        callback_url: Base URL for callbacks.
        base_name: Base filename (technique suffix will be added).
        payload_style: Style of payload content.
        payload_type: Objective of the payload.
        techniques: List of techniques to use (default: all HTML techniques).

        seed: Optional seed for deterministic UUID/token generation.
        encoding: Callback URL encoding ("none", "base16", "hex").
        template: Document context template forwarded to each
            ``create_html`` call so CALLBACK style bodies can interpolate
            the template's ``callback_role``.

    Returns:
        List of Campaign objects.

    Example:
        >>> from q_ai.ipi.generators.html import create_all_html_variants
        >>> campaigns = create_all_html_variants(
        ...     Path("./output"),
        ...     "http://localhost:8080"
        ... )
        >>> len(campaigns)
        4
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    campaigns = []

    if techniques is None:
        techniques = HTML_TECHNIQUES

    for i, technique in enumerate(techniques):
        filename = f"{base_name}_{technique.value}.html"
        output_path = output_dir / filename
        campaign = create_html(
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
            template=template,
        )
        campaigns.append(campaign)

    return campaigns
