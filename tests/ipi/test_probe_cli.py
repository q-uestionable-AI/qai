"""Tests for the IPI probe CLI command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestProbeHelp:
    """Tests for probe command help and discoverability."""

    def test_help_shows_examples(self) -> None:
        result = runner.invoke(app, ["ipi", "probe", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output

    def test_ipi_help_lists_probe(self) -> None:
        result = runner.invoke(app, ["ipi", "--help"])
        assert result.exit_code == 0
        assert "probe" in result.output


class TestProbeDryRun:
    """Tests for probe --dry-run mode."""

    def test_dry_run_lists_probes(self) -> None:
        """--dry-run displays probe table without sending requests."""
        result = runner.invoke(app, ["ipi", "probe", "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
        assert "20" in result.output  # 20 probes

    def test_dry_run_with_custom_probe_set(self, tmp_path: object) -> None:
        """--dry-run works with custom probe set."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            probe_file = Path(td) / "custom.yaml"
            probe_file.write_text(
                "probes:\n"
                "  - id: c-001\n"
                "    category: custom\n"
                '    description: "Custom probe"\n'
                '    system_prompt: "Test"\n'
                '    user_prompt: "Say {canary}"\n'
                '    canary_match: "{canary}"\n'
            )
            result = runner.invoke(
                app, ["ipi", "probe", "--dry-run", "--probe-set", str(probe_file)]
            )
            assert result.exit_code == 0
            assert "1" in result.output  # 1 probe


class TestProbeArgParsing:
    """Tests for probe argument parsing and validation."""

    @patch("q_ai.core.cli.prompt.is_tty", return_value=False)
    def test_no_endpoint_non_tty_fails(self, _mock: object) -> None:
        """Non-TTY with no endpoint fails with clear error."""
        result = runner.invoke(app, ["ipi", "probe", "--model", "test"])
        assert result.exit_code != 0

    @patch("q_ai.core.cli.prompt.is_tty", return_value=False)
    def test_no_model_non_tty_fails(self, _mock: object) -> None:
        """Non-TTY with no model fails with clear error."""
        result = runner.invoke(app, ["ipi", "probe", "http://localhost/v1"])
        assert result.exit_code != 0

    def test_invalid_probe_set_fails(self) -> None:
        """Non-existent probe set file fails gracefully."""
        result = runner.invoke(
            app,
            [
                "ipi",
                "probe",
                "http://localhost/v1",
                "--model",
                "test",
                "--probe-set",
                "/nonexistent/file.yaml",
            ],
        )
        assert result.exit_code != 0
        assert "Error loading probes" in result.output


class TestProbeApiKey:
    """Tests for API key resolution."""

    @patch.dict("os.environ", {"QAI_PROBE_API_KEY": "env-key"})
    def test_env_var_api_key_read(self) -> None:
        """QAI_PROBE_API_KEY env var is accessible."""
        import os

        assert os.environ.get("QAI_PROBE_API_KEY") == "env-key"
