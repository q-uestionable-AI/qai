"""Tests for the IPI document context template registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.ipi.generate_service import GenerateResult, generate_documents
from q_ai.ipi.models import DocumentTemplate, Format, TemplateSpec
from q_ai.ipi.template_registry import (
    BIPIA_COMMIT,
    GARAK_COMMIT,
    TEMPLATE_REGISTRY,
    get_template_spec,
    get_templates_for_format,
)

runner = CliRunner()


class TestRegistryShape:
    """Coverage of every enum member and basic spec shape."""

    def test_registry_contains_every_enum_member(self) -> None:
        assert set(TEMPLATE_REGISTRY) == set(DocumentTemplate)

    def test_registry_has_twelve_members(self) -> None:
        assert len(TEMPLATE_REGISTRY) == 12

    def test_every_spec_marker_present(self) -> None:
        """Non-GENERIC stub templates must contain the {payload} marker."""
        for tmpl, spec in TEMPLATE_REGISTRY.items():
            if tmpl == DocumentTemplate.GENERIC:
                assert spec.context_template == ""
                assert spec.top_instruction == ""
            else:
                assert "{payload}" in spec.context_template

    def test_generic_compatible_with_all_formats(self) -> None:
        spec = TEMPLATE_REGISTRY[DocumentTemplate.GENERIC]
        assert set(spec.formats) == set(Format)

    def test_garak_specs_use_pinned_commit(self) -> None:
        garak_members = {
            DocumentTemplate.WHOIS,
            DocumentTemplate.TRANSLATION_EN_FR,
            DocumentTemplate.TRANSLATION_EN_ZH,
            DocumentTemplate.LEGAL_SNIPPET,
            DocumentTemplate.REPORT,
            DocumentTemplate.RESUME,
        }
        for tmpl in garak_members:
            spec = TEMPLATE_REGISTRY[tmpl]
            assert spec.source_tool == "garak"
            assert spec.source_commit == GARAK_COMMIT

    def test_bipia_specs_use_pinned_commit(self) -> None:
        bipia_members = {
            DocumentTemplate.EMAIL,
            DocumentTemplate.WEB,
            DocumentTemplate.TABLE,
            DocumentTemplate.CODE,
            DocumentTemplate.NEWS,
        }
        for tmpl in bipia_members:
            spec = TEMPLATE_REGISTRY[tmpl]
            assert spec.source_tool == "bipia"
            assert spec.source_commit == BIPIA_COMMIT


class TestAccessors:
    def test_get_template_spec_returns_spec(self) -> None:
        spec = get_template_spec(DocumentTemplate.WHOIS)
        assert isinstance(spec, TemplateSpec)
        assert spec.id == DocumentTemplate.WHOIS

    def test_get_templates_for_format_pdf_includes_whois(self) -> None:
        templates = get_templates_for_format(Format.PDF)
        assert DocumentTemplate.WHOIS in templates
        assert DocumentTemplate.GENERIC in templates

    def test_get_templates_for_format_eml_includes_email(self) -> None:
        templates = get_templates_for_format(Format.EML)
        assert DocumentTemplate.EMAIL in templates

    def test_get_templates_for_format_excludes_incompatible(self) -> None:
        """ICS only matches GENERIC; no document templates target ICS."""
        templates = get_templates_for_format(Format.ICS)
        assert DocumentTemplate.WHOIS not in templates
        assert DocumentTemplate.TABLE not in templates


class TestServiceValidation:
    """Compatibility validation surfaced by ``generate_documents``."""

    def test_incompatible_template_format_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not compatible"):
            generate_documents(
                callback_url="http://localhost:8080",
                output=tmp_path,
                format_name=Format.ICS,
                techniques=[],
                template=DocumentTemplate.TABLE,
            )

    def test_generic_does_not_raise_for_any_format(self, tmp_path: Path) -> None:
        """GENERIC must work for every format (no validation tripwire)."""
        from q_ai.ipi.models import Technique

        # Use a single PDF technique so we exercise the single-fn path.
        result = generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.PDF,
            techniques=[Technique.WHITE_INK],
            template=DocumentTemplate.GENERIC,
            seed=42,
        )
        assert result.campaigns or result.errors


class TestCLIIntegration:
    def test_help_lists_template_option(self) -> None:
        result = runner.invoke(app, ["ipi", "generate", "--help"])
        assert result.exit_code == 0
        assert "--template" in result.output

    @patch("q_ai.ipi.cli.generate_documents")
    def test_template_flag_threads_to_service(self, mock_gen: object) -> None:
        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        result = runner.invoke(
            app,
            [
                "ipi",
                "generate",
                "http://localhost:8080",
                "--template",
                "whois",
            ],
        )
        assert result.exit_code == 0, result.output
        kwargs = mock_gen.call_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["template"] == DocumentTemplate.WHOIS
