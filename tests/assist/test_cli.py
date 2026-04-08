"""Tests for the assist CLI module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from q_ai.assist.cli import app
from q_ai.assist.service import AssistantNotConfiguredError

runner = CliRunner()

_NOT_CONFIGURED_ERROR = AssistantNotConfiguredError(
    "Assistant not configured. Set your provider and model:\n"
    "  qai config set assist.provider ollama && "
    "qai config set assist.model llama3.1"
)


class TestAssistCLI:
    """CLI command tests for qai assist."""

    def test_help_shows_description(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "assistant" in result.output.lower()

    @patch(
        "q_ai.assist.service._resolve_model_string",
        side_effect=_NOT_CONFIGURED_ERROR,
    )
    def test_unconfigured_shows_setup_instructions(self, _mock: MagicMock) -> None:
        """When provider/model not configured, prints setup instructions and exits 1."""
        result = runner.invoke(app, ["-q", "what can qai do?"])
        assert result.exit_code == 1
        assert "not configured" in result.output.lower()
        assert "qai config set" in result.output

    def test_reindex_command_exists(self) -> None:
        result = runner.invoke(app, ["reindex", "--help"])
        assert result.exit_code == 0
        assert "reindex" in result.output.lower()


class TestAssistSingleShot:
    """Single-shot query mode tests."""

    @patch("q_ai.assist.cli._check_configured")
    @patch("q_ai.assist.cli._read_piped_stdin", return_value="")
    @patch("q_ai.assist.cli._run_single_shot")
    def test_query_argument_triggers_single_shot(
        self,
        mock_single: MagicMock,
        _mock_stdin: MagicMock,
        _mock_cfg: MagicMock,
    ) -> None:
        result = runner.invoke(app, ["-q", "what is audit?"])
        assert result.exit_code == 0
        mock_single.assert_called_once()
        args = mock_single.call_args
        assert "what is audit?" in args[0][0]


class TestAssistRunFlag:
    """--run flag tests."""

    @patch("q_ai.assist.cli._check_configured")
    @patch("q_ai.assist.cli._read_piped_stdin", return_value="")
    @patch("q_ai.assist.cli._run_single_shot")
    @patch("q_ai.assist.cli._load_run_context", return_value='{"findings": []}')
    def test_run_flag_loads_context(
        self,
        mock_load: MagicMock,
        mock_single: MagicMock,
        _mock_stdin: MagicMock,
        _mock_cfg: MagicMock,
    ) -> None:
        result = runner.invoke(app, ["--run", "abc123", "-q", "summarize"])
        assert result.exit_code == 0
        mock_load.assert_called_once_with("abc123")
        mock_single.assert_called_once()
        # Second arg should be scan context
        assert '{"findings": []}' in mock_single.call_args[0][1]
