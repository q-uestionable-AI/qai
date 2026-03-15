"""Tests for RXP CLI commands."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from q_ai.rxp.cli import app
from q_ai.rxp.models import ValidationResult

runner = CliRunner()


class TestCLI:
    """Tests for the RXP CLI."""

    def test_rxp_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "list-models" in result.output
        assert "list-profiles" in result.output
        assert "validate" in result.output

    def test_list_models_output(self) -> None:
        result = runner.invoke(app, ["list-models"])
        assert result.exit_code == 0
        assert "minilm-l6" in result.output
        assert "minilm-l12" in result.output
        assert "bge-small" in result.output

    def test_list_profiles_output(self) -> None:
        result = runner.invoke(app, ["list-profiles"])
        assert result.exit_code == 0
        assert "hr-policy" in result.output

    def test_validate_help(self) -> None:
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
        assert "profile" in result.output
        assert "model" in result.output
        assert "top" in result.output

    def test_validate_arbitrary_model_accepted(self) -> None:
        """Arbitrary HuggingFace model names pass CLI argument parsing."""
        result = runner.invoke(
            app, ["validate", "--profile", "hr-policy", "--model", "BAAI/bge-m3"]
        )
        # Should not fail with "Unknown model" — may fail at dep check or model load,
        # but argument parsing accepts arbitrary strings
        assert "Unknown model" not in (result.output or "")

    def test_validate_unknown_profile(self) -> None:
        result = runner.invoke(app, ["validate", "--profile", "fake-profile"])
        assert result.exit_code == 1
        assert "Unknown profile" in result.output


class TestCLIValidate:
    """Tests for the validate command (requires RXP deps)."""

    @pytest.fixture(autouse=True)
    def _check_deps(self) -> None:
        pytest.importorskip("sentence_transformers")
        pytest.importorskip("chromadb")

    def test_validate_runs(self) -> None:
        result = runner.invoke(app, ["validate", "--profile", "hr-policy", "--model", "minilm-l6"])
        assert result.exit_code == 0
        assert "Retrieval rate" in result.output

    def test_validate_output_json(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name
        try:
            result = runner.invoke(
                app,
                [
                    "validate",
                    "--profile",
                    "hr-policy",
                    "--model",
                    "minilm-l6",
                    "--output",
                    output_path,
                ],
            )
            assert result.exit_code == 0
            data = json.loads(Path(output_path).read_text(encoding="utf-8"))
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["model_id"] == "minilm-l6"
        finally:
            Path(output_path).unlink(missing_ok=True)


class TestCLISaveFlag:
    """Tests for the --save flag on validate command."""

    def test_save_flag_persists_to_db(self, tmp_path: Path) -> None:
        """--save calls persist_validation and reports the run ID."""
        import types

        mock_result = ValidationResult(
            model_id="minilm-l6",
            total_queries=3,
            poison_retrievals=2,
            retrieval_rate=0.667,
            mean_poison_rank=2.0,
            query_results=[],
        )

        # Pre-register the validator module so the lazy import inside
        # validate() resolves without needing heavy deps.
        fake_validator = types.ModuleType("q_ai.rxp.validator")
        fake_validator.validate_retrieval = lambda **kwargs: mock_result  # type: ignore[attr-defined]

        with (
            patch("q_ai.rxp._deps.require_rxp_deps"),
            patch.dict("sys.modules", {"q_ai.rxp.validator": fake_validator}),
            patch("q_ai.rxp.mapper.persist_validation", return_value="abc123") as mock_persist,
        ):
            result = runner.invoke(
                app,
                ["validate", "--profile", "hr-policy", "--model", "minilm-l6", "--save"],
            )
            assert result.exit_code == 0
            assert "Saved to database" in result.output
            mock_persist.assert_called_once()

    def test_validate_help_shows_save(self) -> None:
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
        assert "--save" in result.output
