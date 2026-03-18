"""Tests for qai update-frameworks CLI command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.update_frameworks import AtlasDiff, FrameworkStatus

runner = CliRunner()


class TestUpdateFrameworksRegistration:
    """update-frameworks is registered in the root CLI."""

    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["update-frameworks", "--help"])
        assert result.exit_code == 0
        assert "upstream" in result.output.lower() or "framework" in result.output.lower()

    def test_appears_in_root_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "update-frameworks" in result.output


class TestUpdateFrameworksOutput:
    """update-frameworks produces correct Rich table output."""

    @patch("q_ai.core.cli.update_frameworks.check_frameworks")
    def test_displays_summary_table(self, mock_check: object) -> None:
        mock_check_fn = mock_check  # type: ignore[assignment]
        mock_check_fn.return_value = [
            FrameworkStatus(
                framework="mitre_atlas",
                local_version="v5.4.0",
                upstream_version="v5.5.0",
                status="update-available",
                message="Version delta: v5.4.0 -> v5.5.0",
                atlas_diff=AtlasDiff(
                    new_techniques=["AML.T0100"],
                    deprecated_techniques=[],
                ),
            ),
            FrameworkStatus(
                framework="owasp_mcp_top10",
                local_version="2025-beta",
                upstream_version="2025-beta",
                status="up-to-date",
                message="Version unchanged",
            ),
        ]

        result = runner.invoke(app, ["update-frameworks"])
        assert result.exit_code == 0
        assert "mitre_atlas" in result.output
        assert "owasp_mcp_top10" in result.output
        assert "v5.4.0" in result.output
        assert "v5.5.0" in result.output

    @patch("q_ai.core.cli.update_frameworks.check_frameworks")
    def test_atlas_flag_shows_diff(self, mock_check: object) -> None:
        mock_check_fn = mock_check  # type: ignore[assignment]
        mock_check_fn.return_value = [
            FrameworkStatus(
                framework="mitre_atlas",
                local_version="v5.4.0",
                upstream_version="v5.5.0",
                status="update-available",
                message="1 new technique(s)",
                atlas_diff=AtlasDiff(
                    new_techniques=["AML.T0100"],
                    deprecated_techniques=["AML.T0001"],
                ),
            ),
        ]

        result = runner.invoke(app, ["update-frameworks", "--atlas"])
        assert result.exit_code == 0
        assert "AML.T0100" in result.output
        assert "AML.T0001" in result.output

    @patch("q_ai.core.cli.update_frameworks.check_frameworks")
    def test_error_status_displayed(self, mock_check: object) -> None:
        mock_check_fn = mock_check  # type: ignore[assignment]
        mock_check_fn.return_value = [
            FrameworkStatus(
                framework="mitre_atlas",
                local_version="v5.4.0",
                upstream_version="unknown",
                status="error",
                message="Failed to fetch ATLAS release info",
            ),
        ]

        result = runner.invoke(app, ["update-frameworks"])
        assert result.exit_code == 0
        assert "Failed to fetch" in result.output
