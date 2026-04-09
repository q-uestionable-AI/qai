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
