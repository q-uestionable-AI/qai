"""Tests for qai db CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import (
    create_run,
    create_target,
    get_connection,
)

runner = CliRunner()


class TestDbBackup:
    def test_db_backup(self, tmp_path: Path) -> None:
        """qai db backup creates a backup file."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path):
            pass  # initialize DB

        dest = tmp_path / "backup.db"
        result = runner.invoke(app, ["db", "backup", str(dest), "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "Backup created:" in result.output
        assert dest.exists()


class TestDbReset:
    def test_db_reset_with_yes(self, tmp_path: Path) -> None:
        """qai db reset --yes clears data and reports completion."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            tid = create_target(conn, type="server", name="t1", uri="http://x")
            create_run(conn, module="audit", target_id=tid)

        result = runner.invoke(app, ["db", "reset", "--yes", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "Database reset complete." in result.output
        assert "Backup created:" in result.output

        # Verify data was cleared
        with get_connection(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    def test_db_reset_prompts_without_yes(self, tmp_path: Path) -> None:
        """qai db reset without --yes aborts when user declines."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path):
            pass

        result = runner.invoke(
            app,
            ["db", "reset", "--db-path", str(db_path)],
            input="n\n",
        )
        # Should abort (typer.confirm abort raises SystemExit → non-zero)
        assert result.exit_code != 0
