"""Tests for Technique.NONE — the visible-payload control condition.

These tests verify that NONE:
- exists as a valid enum member;
- is reported by ``get_techniques_for_format`` for every format;
- is excluded from the ``all`` / ``phase1`` / ``phase2`` CLI presets
  but accepted as a direct technique name;
- generates a document containing the visible payload text for every
  supported format.
"""

from __future__ import annotations

from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path

import pytest

from q_ai.ipi.cli import parse_techniques
from q_ai.ipi.generators import TECHNIQUE_FORMATS, get_techniques_for_format
from q_ai.ipi.generators.docx import create_docx
from q_ai.ipi.generators.eml import create_eml
from q_ai.ipi.generators.html import create_html
from q_ai.ipi.generators.ics import create_ics
from q_ai.ipi.generators.image import create_image
from q_ai.ipi.generators.markdown import create_markdown
from q_ai.ipi.generators.pdf import (
    PDF_ALL_TECHNIQUES,
    PDF_PHASE1_TECHNIQUES,
    PDF_PHASE2_TECHNIQUES,
    create_pdf,
)
from q_ai.ipi.models import Format, Technique

CALLBACK = "http://localhost:8080"
SEED = 1234


class TestTechniqueNoneEnum:
    """Enum-level properties of Technique.NONE."""

    def test_enum_value(self) -> None:
        assert Technique.NONE.value == "none"

    def test_roundtrip_from_string(self) -> None:
        assert Technique("none") is Technique.NONE

    def test_not_in_technique_formats_registry(self) -> None:
        # Per design note in the task brief: NONE is not registered in
        # TECHNIQUE_FORMATS; get_techniques_for_format appends it manually.
        assert Technique.NONE not in TECHNIQUE_FORMATS

    def test_not_in_pdf_hiding_technique_lists(self) -> None:
        assert Technique.NONE not in PDF_PHASE1_TECHNIQUES
        assert Technique.NONE not in PDF_PHASE2_TECHNIQUES
        assert Technique.NONE not in PDF_ALL_TECHNIQUES


class TestGetTechniquesForFormat:
    """``get_techniques_for_format`` must include NONE for every format."""

    @pytest.mark.parametrize("fmt", list(Format))
    def test_every_format_includes_none(self, fmt: Format) -> None:
        assert Technique.NONE in get_techniques_for_format(fmt)

    def test_pdf_has_eleven_techniques(self) -> None:
        # 10 PDF hiding techniques + NONE.
        assert len(get_techniques_for_format(Format.PDF)) == 11


class TestCliPresetExclusion:
    """CLI presets must exclude NONE but accept it as a direct name."""

    def test_all_preset_excludes_none(self) -> None:
        assert Technique.NONE not in parse_techniques("all")

    def test_phase1_preset_excludes_none(self) -> None:
        assert Technique.NONE not in parse_techniques("phase1")

    def test_phase2_preset_excludes_none(self) -> None:
        assert Technique.NONE not in parse_techniques("phase2")

    def test_direct_none_is_accepted(self) -> None:
        assert parse_techniques("none") == [Technique.NONE]

    def test_comma_separated_with_none(self) -> None:
        result = parse_techniques("white_ink,none")
        assert Technique.WHITE_INK in result
        assert Technique.NONE in result


class TestGeneratorsRenderVisiblePayload:
    """Each generator must produce a file containing the visible payload."""

    def test_pdf(self, tmp_path: Path) -> None:
        import pypdf

        output = tmp_path / "control.pdf"
        campaign = create_pdf(output, Technique.NONE, CALLBACK, seed=SEED)
        assert output.exists()
        assert campaign.technique == Technique.NONE
        # The payload must appear as visible text on the page.
        reader = pypdf.PdfReader(str(output))
        extracted = "".join(page.extract_text() or "" for page in reader.pages)
        assert campaign.uuid in extracted

    def test_markdown(self, tmp_path: Path) -> None:
        output = tmp_path / "control.md"
        campaign = create_markdown(output, Technique.NONE, CALLBACK, seed=SEED)
        text = output.read_text(encoding="utf-8")
        assert campaign.uuid in text
        # Must not wrap the payload in a hiding construct.
        assert "<!--" not in text
        assert "display:none" not in text

    def test_html(self, tmp_path: Path) -> None:
        output = tmp_path / "control.html"
        campaign = create_html(output, Technique.NONE, CALLBACK, seed=SEED)
        text = output.read_text(encoding="utf-8")
        assert campaign.uuid in text
        # Payload must not be hidden by CSS or placed in a data-attribute.
        assert "display:none" not in text
        assert f'data-config="{campaign.uuid}' not in text

    def test_docx(self, tmp_path: Path) -> None:
        from docx import Document

        output = tmp_path / "control.docx"
        campaign = create_docx(output, Technique.NONE, CALLBACK, seed=SEED)
        doc = Document(str(output))
        body_text = "\n".join(p.text for p in doc.paragraphs)
        assert campaign.uuid in body_text

    def test_image(self, tmp_path: Path) -> None:
        output = tmp_path / "control.png"
        campaign = create_image(output, Technique.NONE, CALLBACK, seed=SEED)
        assert output.exists()
        assert campaign.technique == Technique.NONE
        # The image bytes will differ from the decoy-only baseline because
        # the payload is rendered into the pixels.
        from q_ai.ipi.generators.image import _create_base_image

        baseline_bytes = tmp_path / "baseline.png"
        _create_base_image((800, 600), "Receipt").save(baseline_bytes)
        assert output.read_bytes() != baseline_bytes.read_bytes()

    def test_eml(self, tmp_path: Path) -> None:
        output = tmp_path / "control.eml"
        campaign = create_eml(output, Technique.NONE, CALLBACK, seed=SEED)
        msg = message_from_bytes(output.read_bytes(), policy=default_policy)
        body = msg.get_body(preferencelist=("plain",))
        assert body is not None
        assert campaign.uuid in body.get_content()

    def test_ics(self, tmp_path: Path) -> None:
        output = tmp_path / "control.ics"
        campaign = create_ics(output, Technique.NONE, CALLBACK, seed=SEED)
        # iCalendar line-folds long values with "\r\n " / "\n " sequences
        # (RFC 5545 §3.1). Unfold before matching.
        unfolded = output.read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
        assert campaign.uuid in unfolded
        assert "DESCRIPTION" in unfolded
