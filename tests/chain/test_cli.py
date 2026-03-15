"""Tests for qai chain CLI subcommands."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

import q_ai.chain.templates as _tpkg
from q_ai.chain.cli import app

runner = CliRunner()

_BUILTIN_DIR = Path(_tpkg.__file__).parent


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _builtin_path(name: str) -> str:
    """Return string path to a built-in template."""
    return str(_BUILTIN_DIR / name)


class TestChainHelp:
    """chain --help shows all subcommands."""

    def test_chain_help(self) -> None:
        """All five subcommands appear in help output."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "run" in plain
        assert "list-templates" in plain
        assert "validate" in plain
        assert "blast-radius" in plain
        assert "detect" in plain


class TestValidate:
    """chain validate subcommand."""

    def test_validate_help(self) -> None:
        """Help text includes --chain-file option."""
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
        assert "--chain-file" in _strip_ansi(result.output)

    def test_validate_valid_builtin(self) -> None:
        """validate with a valid built-in template exits 0."""
        result = runner.invoke(
            app, ["validate", "--chain-file", _builtin_path("rag_trust_escalation.yaml")]
        )
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_validate_invalid_file(self, tmp_path: Path) -> None:
        """validate with invalid technique exits 1 with error messages."""
        p = tmp_path / "bad.yaml"
        p.write_text(
            "id: bad\nname: Bad\ncategory: rag_pipeline\n"
            "description: Bad\nsteps:\n"
            "  - id: s1\n    name: S\n    module: inject\n"
            "    technique: fake_technique\n    terminal: true\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", "--chain-file", str(p)])
        assert result.exit_code == 1

    def test_validate_missing_file(self, tmp_path: Path) -> None:
        """Nonexistent path exits 1."""
        missing = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["validate", "--chain-file", str(missing)])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestListTemplates:
    """chain list-templates subcommand."""

    def test_list_templates_shows_all(self) -> None:
        """All 3 built-in templates listed."""
        result = runner.invoke(app, ["list-templates"])
        assert result.exit_code == 0
        output = result.output
        assert "rag-trust-escalation" in output
        assert "mcp-server-compromise" in output
        assert "delegation-hijack" in output

    def test_list_templates_filter_category(self) -> None:
        """--category rag_pipeline shows only 1 template."""
        result = runner.invoke(app, ["list-templates", "--category", "rag_pipeline"])
        assert result.exit_code == 0
        assert "rag-trust-escalation" in result.output
        assert "delegation-hijack" not in result.output


class TestRun:
    """chain run subcommand."""

    def test_run_help(self) -> None:
        """Help text includes all run options."""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.output)
        assert "--chain-file" in plain
        assert "--dry-run" in plain
        assert "--output" in plain
        assert "--targets" in plain
        assert "--inject-model" in plain

    def test_run_dry_run_valid(self) -> None:
        """run --dry-run with valid template exits 0, shows trace."""
        result = runner.invoke(
            app,
            ["run", "--chain-file", _builtin_path("rag_trust_escalation.yaml"), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "poison-tool" in result.output

    def test_run_dry_run_output_json(self, tmp_path: Path) -> None:
        """--output writes JSON file."""
        out = tmp_path / "trace.json"
        result = runner.invoke(
            app,
            [
                "run",
                "--chain-file",
                _builtin_path("rag_trust_escalation.yaml"),
                "--dry-run",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["chain_id"] == "rag-trust-escalation"
        assert len(data["steps"]) == 3

    def test_run_live_requires_targets(self) -> None:
        """run --no-dry-run without targets file exits 1 with clear error."""
        result = runner.invoke(
            app,
            ["run", "--chain-file", _builtin_path("rag_trust_escalation.yaml"), "--no-dry-run"],
        )
        assert result.exit_code == 1
        plain = _strip_ansi(result.output)
        assert "targets" in plain.lower() or "error" in plain.lower()

    def test_run_live_missing_targets_file(self, tmp_path: Path) -> None:
        """run --no-dry-run with nonexistent --targets exits 1."""
        missing = tmp_path / "nonexistent.yaml"
        result = runner.invoke(
            app,
            [
                "run",
                "--chain-file",
                _builtin_path("rag_trust_escalation.yaml"),
                "--no-dry-run",
                "--targets",
                str(missing),
            ],
        )
        assert result.exit_code == 1
        plain = _strip_ansi(result.output)
        assert "not found" in plain.lower() or "error" in plain.lower()
