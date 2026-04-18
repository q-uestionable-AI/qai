"""Tests for the IPI CLI commands."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestIpiGenerateCallbackArg:
    """Tests for callback as positional arg in ipi generate."""

    def test_help_shows_examples(self) -> None:
        result = runner.invoke(app, ["ipi", "generate", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output

    @patch("q_ai.core.cli.prompt.is_tty", return_value=False)
    def test_no_callback_non_tty_fails(self, _mock: object) -> None:
        """Non-TTY with no callback fails with clear error."""
        result = runner.invoke(app, ["ipi", "generate"])
        assert result.exit_code != 0

    @patch("q_ai.ipi.cli.generate_documents")
    def test_positional_callback(self, mock_gen: object) -> None:
        """Callback can be passed as first positional argument."""
        from q_ai.ipi.generate_service import GenerateResult

        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        result = runner.invoke(app, ["ipi", "generate", "http://localhost:8080"])
        assert result.exit_code == 0

    @patch("q_ai.ipi.cli.generate_documents")
    def test_callback_option_flag(self, mock_gen: object) -> None:
        """--callback flag still works for backward compatibility."""
        from q_ai.ipi.generate_service import GenerateResult

        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        result = runner.invoke(app, ["ipi", "generate", "--callback", "http://localhost:8080"])
        assert result.exit_code == 0


class TestIpiHelpText:
    """Token-level assertions guarding against `--help` text drift.

    These tests pin the key corrections from the 2026-04-17 help-text
    sweep so future additions don't silently regress the help surface.
    Assert on tokens, not full strings, so Typer formatting changes
    (wrapping, whitespace, trailing punctuation) don't break them.
    """

    def test_techniques_help_enumerates_all_seven_formats(self) -> None:
        """`qai ipi techniques --help` must list every supported format."""
        result = runner.invoke(app, ["ipi", "techniques", "--help"])
        assert result.exit_code == 0
        for fmt in ("pdf", "image", "markdown", "html", "docx", "ics", "eml"):
            assert fmt in result.output, f"format {fmt!r} missing from techniques --help"

    def test_generate_technique_help_mentions_none(self) -> None:
        """`qai ipi generate --help` must mention `none` as a technique option."""
        result = runner.invoke(app, ["ipi", "generate", "--help"])
        assert result.exit_code == 0
        assert "none" in result.output, "`none` control condition not mentioned in generate --help"

    def test_probe_export_help_references_scored_prompts(self) -> None:
        """`qai ipi probe --help` must describe --export as scored-prompts JSON."""
        result = runner.invoke(app, ["ipi", "probe", "--help"])
        assert result.exit_code == 0
        # Accept either hyphenated or space-separated form to tolerate Typer wrap.
        assert "scored-prompts" in result.output or "scored prompts" in result.output, (
            "probe --export help does not reference scored-prompts JSON"
        )
