"""Tests for the CTPF Research Harness root CLI."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()

_COMMAND_NAMES = ("ctpf", "qai")
_DISPLAY_NAME = "CTPF Research Harness"
_SUBTITLE = "Trust-boundary testing for agentic systems"


class TestCLIHelp:
    """Root help shows the CTPF identity and transitional commands."""

    @pytest.mark.parametrize("command_name", _COMMAND_NAMES)
    def test_help_exits_zero(self, command_name: str) -> None:
        result = runner.invoke(app, ["--help"], prog_name=command_name)
        assert result.exit_code == 0
        assert _DISPLAY_NAME in result.output
        assert _SUBTITLE in result.output

    def test_help_shows_transitional_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("proxy", "targets", "runs", "findings", "config", "db"):
            assert cmd in result.output

    def test_help_hides_removed_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in (
            "ui",
            "audit",
            "assist",
            "rxp",
            "inject",
            "ipi",
            "chain",
            "cxp",
            "imports",
            "orchestrator",
        ):
            # Match as top-level command tokens, not substrings in longer words
            assert f"  {cmd} " not in result.output
            assert f"  {cmd}\n" not in result.output


class TestCLIVersion:
    """Version output reflects the invoked compatibility entry point."""

    @pytest.mark.parametrize("command_name", _COMMAND_NAMES)
    def test_version_exits_zero(self, command_name: str) -> None:
        from q_ai import __version__

        result = runner.invoke(app, ["--version"], prog_name=command_name)
        assert result.exit_code == 0
        assert f"{command_name} {__version__}" in result.output

    @pytest.mark.parametrize("command_name", _COMMAND_NAMES)
    def test_version_short_flag(self, command_name: str) -> None:
        from q_ai import __version__

        result = runner.invoke(app, ["-V"], prog_name=command_name)
        assert result.exit_code == 0
        assert f"{command_name} {__version__}" in result.output


class TestBareCLI:
    """Bare `ctpf` and `qai` invocations print matching help screens."""

    @pytest.mark.parametrize("command_name", _COMMAND_NAMES)
    def test_no_args_prints_invocation_aware_help(self, command_name: str) -> None:
        result = runner.invoke(app, [], prog_name=command_name)
        assert result.exit_code == 0
        normalized_output = " ".join(result.output.split())
        assert "Quick Start" in result.output
        assert _DISPLAY_NAME in result.output
        assert _SUBTITLE in normalized_output
        assert f"{command_name} proxy" in result.output
        assert f"{command_name} targets" in result.output

    def test_no_args_has_no_ui_hint(self) -> None:
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "qai ui" not in result.output

    def test_subcommands_still_work(self) -> None:
        result = runner.invoke(app, ["runs", "--help"])
        assert result.exit_code == 0
        assert "runs" in result.output.lower()


class TestRemovedUiCommand:
    """qai ui is no longer registered."""

    def test_ui_command_absent(self) -> None:
        result = runner.invoke(app, ["ui"])
        assert result.exit_code != 0


class TestGroupedHelp:
    """Root --help shows grouped command panels."""

    def test_help_shows_observe_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Observe" in result.output

    def test_help_shows_start_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Start" in result.output

    def test_help_shows_manage_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Manage" in result.output

    def test_help_has_no_modules_group(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Modules" not in result.output


def test_distribution_exposes_preferred_and_compatibility_entry_points() -> None:
    """Project metadata maps both executable names to the same Typer app."""
    project_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    scripts = pyproject["project"]["scripts"]
    assert scripts["ctpf"] == "q_ai.cli:app"
    assert scripts["qai"] == scripts["ctpf"]
