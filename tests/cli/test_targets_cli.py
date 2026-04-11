"""Tests for qai targets delete CLI command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import create_target, get_connection

runner = CliRunner()


class TestTargetsDelete:
    """Tests for qai targets delete command."""

    def test_targets_delete_with_yes(self, tmp_path: Path) -> None:
        """qai targets delete --yes removes the target."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            tid = create_target(conn, type="server", name="My Server", uri="http://x")

        result = runner.invoke(app, ["targets", "delete", tid, "--yes", "--db-path", str(db_path)])
        assert result.exit_code == 0
        assert "Deleted target 'My Server'" in result.output

        with get_connection(db_path) as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM targets WHERE id = ?", (tid,)).fetchone()[0] == 0
            )

    def test_targets_delete_partial_id(self, tmp_path: Path) -> None:
        """qai targets delete with partial ID resolves and deletes."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path) as conn:
            tid = create_target(conn, type="server", name="Partial", uri="http://x")

        result = runner.invoke(
            app, ["targets", "delete", tid[:8], "--yes", "--db-path", str(db_path)]
        )
        assert result.exit_code == 0
        assert "Deleted target 'Partial'" in result.output

    def test_targets_delete_not_found(self, tmp_path: Path) -> None:
        """qai targets delete with nonexistent ID reports an error."""
        db_path = tmp_path / "qai.db"
        with get_connection(db_path):
            pass

        result = runner.invoke(
            app, ["targets", "delete", "deadbeef", "--yes", "--db-path", str(db_path)]
        )
        assert result.exit_code != 0
        assert "Error" in result.output
