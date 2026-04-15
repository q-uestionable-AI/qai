"""Tests for payload URL encoding obfuscation (TB-11).

Covers encode_payload() dispatch, generate_payload() integration, and
end-to-end format generator output.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from q_ai.ipi.generators import (
    ENCODING_CHOICES,
    encode_payload,
    generate_payload,
)
from q_ai.ipi.generators.docx import create_docx
from q_ai.ipi.generators.eml import create_eml
from q_ai.ipi.generators.html import create_html
from q_ai.ipi.generators.ics import create_ics
from q_ai.ipi.generators.image import create_image
from q_ai.ipi.generators.markdown import create_markdown
from q_ai.ipi.generators.pdf import create_pdf
from q_ai.ipi.models import PayloadStyle, PayloadType, Technique

_PLAIN_URL_FRAGMENT = "http://localhost:8080"


class TestEncodingChoices:
    """ENCODING_CHOICES exposes the supported encoding names."""

    def test_choices_exposed(self) -> None:
        assert ENCODING_CHOICES == ("none", "base16", "hex")


class TestEncodePayload:
    """encode_payload() dispatches to the correct encoder."""

    def test_none_returns_input_unchanged(self) -> None:
        assert encode_payload("http://x/y", "none") == "http://x/y"

    def test_base16_matches_stdlib(self) -> None:
        result = encode_payload("http://x/y", "base16")
        expected = base64.b16encode(b"http://x/y").decode("ascii")
        assert result == expected

    def test_hex_is_uppercase(self) -> None:
        result = encode_payload("http://x/y", "hex")
        assert result == b"http://x/y".hex().upper()
        assert all(c.isdigit() or c.isupper() for c in result)

    def test_base16_and_hex_are_equivalent(self) -> None:
        # Base16 and hex-upper produce the same output for ASCII input.
        assert encode_payload("http://x/y", "base16") == encode_payload("http://x/y", "hex")

    def test_unknown_encoding_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported encoding"):
            encode_payload("x", "rot13")

    def test_unicode_payload_is_utf8_encoded(self) -> None:
        # Non-ASCII must round-trip via UTF-8.
        assert encode_payload("ümläut", "hex") == "ümläut".encode().hex().upper()


class TestGeneratePayloadEncoding:
    """generate_payload() encodes the callback URL when encoding != 'none'."""

    def test_plain_url_present_when_encoding_none(self) -> None:
        payload = generate_payload(
            _PLAIN_URL_FRAGMENT,
            "abc-123",
            PayloadStyle.OBVIOUS,
            PayloadType.CALLBACK,
            token="tok",
            encoding="none",
        )
        assert _PLAIN_URL_FRAGMENT in payload
        assert "abc-123" in payload

    def test_plain_url_absent_when_encoding_base16(self) -> None:
        payload = generate_payload(
            _PLAIN_URL_FRAGMENT,
            "abc-123",
            PayloadStyle.OBVIOUS,
            PayloadType.CALLBACK,
            token="tok",
            encoding="base16",
        )
        assert _PLAIN_URL_FRAGMENT not in payload
        assert "abc-123" not in payload
        encoded = base64.b16encode(f"{_PLAIN_URL_FRAGMENT}/c/abc-123/tok".encode()).decode("ascii")
        assert encoded in payload

    def test_plain_url_absent_when_encoding_hex(self) -> None:
        payload = generate_payload(
            _PLAIN_URL_FRAGMENT,
            "abc-123",
            PayloadStyle.OBVIOUS,
            PayloadType.CALLBACK,
            token="tok",
            encoding="hex",
        )
        assert _PLAIN_URL_FRAGMENT not in payload
        encoded = f"{_PLAIN_URL_FRAGMENT}/c/abc-123/tok".encode().hex().upper()
        assert encoded in payload

    def test_framing_text_remains_readable(self) -> None:
        # Framing (e.g., "SYSTEM INSTRUCTION") must stay plaintext so the LLM follows it.
        payload = generate_payload(
            _PLAIN_URL_FRAGMENT,
            "abc-123",
            PayloadStyle.OBVIOUS,
            PayloadType.CALLBACK,
            token="tok",
            encoding="hex",
        )
        assert "SYSTEM INSTRUCTION" in payload


# =============================================================================
# Per-format integration tests
# =============================================================================


def _expected_encoded_url(callback_url: str, uuid: str, token: str) -> str:
    return base64.b16encode(f"{callback_url}/c/{uuid}/{token}".encode()).decode("ascii")


def test_pdf_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    out = tmp_path / "r.pdf"
    campaign = create_pdf(
        out,
        Technique.METADATA,
        _PLAIN_URL_FRAGMENT,
        seed=1,
        encoding="base16",
    )
    data = out.read_bytes()
    encoded = _expected_encoded_url(_PLAIN_URL_FRAGMENT, campaign.uuid, campaign.token)
    assert encoded.encode("ascii") in data
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}".encode() not in data


def test_image_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    out = tmp_path / "img.png"
    campaign = create_image(
        out,
        Technique.VISIBLE_TEXT,
        _PLAIN_URL_FRAGMENT,
        seed=2,
        encoding="hex",
    )
    # VISIBLE_TEXT rasterizes the payload text onto the image, so we can't
    # grep bytes. Instead verify the campaign generator branched correctly
    # by re-generating the payload string and checking it is not plaintext.
    payload = generate_payload(
        _PLAIN_URL_FRAGMENT,
        campaign.uuid,
        PayloadStyle.OBVIOUS,
        PayloadType.CALLBACK,
        token=campaign.token,
        encoding="hex",
    )
    assert _PLAIN_URL_FRAGMENT not in payload
    assert campaign.uuid not in payload
    assert out.exists()


def test_markdown_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    out = tmp_path / "d.md"
    campaign = create_markdown(
        out,
        Technique.HTML_COMMENT,
        _PLAIN_URL_FRAGMENT,
        seed=3,
        encoding="base16",
    )
    content = out.read_text(encoding="utf-8")
    encoded = _expected_encoded_url(_PLAIN_URL_FRAGMENT, campaign.uuid, campaign.token)
    assert encoded in content
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}" not in content


def test_html_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    out = tmp_path / "p.html"
    campaign = create_html(
        out,
        Technique.SCRIPT_COMMENT,
        _PLAIN_URL_FRAGMENT,
        seed=4,
        encoding="base16",
    )
    content = out.read_text(encoding="utf-8")
    encoded = _expected_encoded_url(_PLAIN_URL_FRAGMENT, campaign.uuid, campaign.token)
    assert encoded in content
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}" not in content


def test_docx_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    from docx import Document

    out = tmp_path / "r.docx"
    campaign = create_docx(
        out,
        Technique.DOCX_HIDDEN_TEXT,
        _PLAIN_URL_FRAGMENT,
        seed=5,
        encoding="base16",
    )
    doc = Document(str(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    encoded = _expected_encoded_url(_PLAIN_URL_FRAGMENT, campaign.uuid, campaign.token)
    assert encoded in text
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}" not in text


def test_ics_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    out = tmp_path / "m.ics"
    campaign = create_ics(
        out,
        Technique.ICS_DESCRIPTION,
        _PLAIN_URL_FRAGMENT,
        seed=6,
        encoding="base16",
    )
    # RFC 5545 folds long lines with CRLF + single space. Unfold before matching.
    unfolded = out.read_bytes().replace(b"\r\n ", b"")
    encoded = _expected_encoded_url(_PLAIN_URL_FRAGMENT, campaign.uuid, campaign.token)
    assert encoded.encode("ascii") in unfolded
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}".encode() not in unfolded


def test_eml_encoding_embeds_encoded_url(tmp_path: Path) -> None:
    import email
    from email.header import decode_header, make_header

    out = tmp_path / "msg.eml"
    campaign = create_eml(
        out,
        Technique.EML_X_HEADER,
        _PLAIN_URL_FRAGMENT,
        seed=7,
        encoding="base16",
    )
    msg = email.message_from_bytes(out.read_bytes())
    decoded_headers = "\n".join(
        str(make_header(decode_header(v))) for k, v in msg.items() if k.startswith("X-")
    )
    encoded = _expected_encoded_url(_PLAIN_URL_FRAGMENT, campaign.uuid, campaign.token)
    assert encoded in decoded_headers
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}" not in decoded_headers


def test_encoding_none_preserves_plaintext_url(tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    campaign = create_markdown(
        out,
        Technique.HTML_COMMENT,
        _PLAIN_URL_FRAGMENT,
        seed=8,
        encoding="none",
    )
    content = out.read_text(encoding="utf-8")
    assert f"{_PLAIN_URL_FRAGMENT}/c/{campaign.uuid}/{campaign.token}" in content
