"""Tests for qai config CLI commands."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestConfigSet:
    def test_set_value(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        result = runner.invoke(
            app, ["config", "set", "audit.default_transport", "stdio", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Set" in result.output


class TestConfigGet:
    def test_get_not_set(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        result = runner.invoke(
            app, ["config", "get", "nonexistent", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "not set" in result.output

    def test_set_then_get_roundtrip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        runner.invoke(
            app, ["config", "set", "audit.default_transport", "stdio", "--db-path", str(db_path)],
        )
        result = runner.invoke(
            app, ["config", "get", "audit.default_transport", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "stdio" in result.output
        assert "db" in result.output


class TestConfigSetCredential:
    def test_set_credential(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        result = runner.invoke(
            app, [
                "config", "set-credential",
                "anthropic", "sk-test-key",
                "--config-path", str(config_path),
            ],
        )
        assert result.exit_code == 0
        assert "saved" in result.output
        # Verify file was created
        assert config_path.exists()
