"""Tests for qai config CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from q_ai.cli import app

runner = CliRunner()


class TestConfigSet:
    def test_set_value(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        result = runner.invoke(
            app,
            ["config", "set", "audit.default_transport", "stdio", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "Set" in result.output


class TestConfigGet:
    def test_get_not_set(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        result = runner.invoke(
            app,
            ["config", "get", "nonexistent", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "not set" in result.output

    def test_set_then_get_roundtrip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qai.db"
        runner.invoke(
            app,
            ["config", "set", "audit.default_transport", "stdio", "--db-path", str(db_path)],
        )
        result = runner.invoke(
            app,
            ["config", "get", "audit.default_transport", "--db-path", str(db_path)],
        )
        assert result.exit_code == 0
        assert "stdio" in result.output
        assert "db" in result.output


class TestConfigSetCredential:
    def test_set_credential(self) -> None:
        with (
            patch("q_ai.core.cli.config.set_credential") as mock_set,
            patch("getpass.getpass", return_value="sk-test-key"),
        ):
            result = runner.invoke(
                app,
                ["config", "set-credential", "anthropic"],
            )
        assert result.exit_code == 0
        assert "saved" in result.output
        mock_set.assert_called_once_with("anthropic", "sk-test-key")

    def test_set_credential_empty_key_errors(self) -> None:
        with patch("getpass.getpass", return_value=""):
            result = runner.invoke(
                app,
                ["config", "set-credential", "anthropic"],
            )
        assert result.exit_code == 1
        assert "Empty" in result.output


class TestConfigDeleteCredential:
    def test_delete_credential(self) -> None:
        with patch("q_ai.core.cli.config.delete_credential") as mock_del:
            result = runner.invoke(
                app,
                ["config", "delete-credential", "anthropic"],
            )
        assert result.exit_code == 0
        assert "removed" in result.output
        mock_del.assert_called_once_with("anthropic")


class TestConfigListProviders:
    def test_list_providers(self) -> None:
        with patch("q_ai.core.cli.config.get_credential") as mock_get:
            mock_get.side_effect = lambda p: "key" if p == "anthropic" else None
            result = runner.invoke(app, ["config", "list-providers"])
        assert result.exit_code == 0
        assert "anthropic" in result.output


class TestConfigImportLegacy:
    def test_import_no_legacy(self) -> None:
        with patch("q_ai.core.cli.config.import_legacy_credentials", return_value=[]):
            result = runner.invoke(app, ["config", "import-legacy-credentials"])
        assert result.exit_code == 0
        assert "No legacy" in result.output
