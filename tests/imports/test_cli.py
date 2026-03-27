"""Tests for the import CLI command."""

import re
from pathlib import Path

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.db import get_connection, list_findings, list_runs

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_import_garak(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    result = runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "garak_report.jsonl"),
            "--format",
            "garak",
            "--db-path",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Imported 3 findings" in result.output

    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 1
        assert runs[0].source == "garak"
        assert runs[0].config is not None
        assert runs[0].config.get("importer_version") is not None
        assert runs[0].config.get("source_checksum") is not None

        findings = list_findings(conn, run_id=runs[0].id)
        assert len(findings) == 3


def test_import_pyrit(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    result = runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "pyrit_conversations.json"),
            "--format",
            "pyrit",
            "--db-path",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Imported 3 findings" in result.output

    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 1
        assert runs[0].source == "pyrit"

        findings = list_findings(conn, run_id=runs[0].id)
        assert len(findings) == 3


def test_import_sarif(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    result = runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "report.sarif"),
            "--format",
            "sarif",
            "--db-path",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Imported 3 findings" in result.output

    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 1
        assert runs[0].source == "SecurityScanner"

        findings = list_findings(conn, run_id=runs[0].id)
        assert len(findings) == 3


def test_import_dry_run(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    result = runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "garak_report.jsonl"),
            "--format",
            "garak",
            "--dry-run",
            "--db-path",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry Run" in result.output
    assert "3 findings would be imported" in result.output

    # DB should not have any runs.
    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 0


def test_import_unknown_format() -> None:
    result = runner.invoke(
        app, ["import", str(FIXTURES / "garak_report.jsonl"), "--format", "nope"]
    )
    assert result.exit_code == 1
    assert "Unknown format" in result.output


def test_import_malformed_garak() -> None:
    result = runner.invoke(
        app, ["import", str(FIXTURES / "malformed_garak.jsonl"), "--format", "garak"]
    )
    assert result.exit_code == 1
    assert "Import failed" in result.output


def test_import_evidence_stored(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "report.sarif"),
            "--format",
            "sarif",
            "--db-path",
            str(db),
        ],
    )
    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 1
        # Check evidence records exist.
        cursor = conn.execute("SELECT type FROM evidence WHERE run_id = ?", (runs[0].id,))
        types = {row["type"] for row in cursor.fetchall()}
        assert "import_raw" in types
        assert "import_metadata" in types


def test_qai_import_help() -> None:
    result = runner.invoke(app, ["import", "--help"])
    assert result.exit_code == 0
    plain = _strip_ansi(result.output)
    assert "--format" in plain
    assert "--dry-run" in plain


def test_import_pyrit_json_object_not_list(tmp_path: Path) -> None:
    """PyRIT file containing a JSON object instead of a list should fail."""
    bad = tmp_path / "obj.json"
    bad.write_text('{"key": "value"}', encoding="utf-8")
    result = runner.invoke(app, ["import", str(bad), "--format", "pyrit"])
    assert result.exit_code == 1
    assert "Import failed" in result.output


def test_import_sarif_json_array_not_object(tmp_path: Path) -> None:
    """SARIF file containing a JSON array instead of an object should fail."""
    bad = tmp_path / "arr.sarif"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    result = runner.invoke(app, ["import", str(bad), "--format", "sarif"])
    assert result.exit_code == 1
    assert "Import failed" in result.output


def test_import_with_target(tmp_path: Path) -> None:
    """--target sets target_id on the import run."""
    db = tmp_path / "test.db"

    # Create a target first so FK constraint is satisfied
    with get_connection(db) as conn:
        conn.execute(
            "INSERT INTO targets (id, type, name, created_at) VALUES (?, ?, ?, ?)",
            ("tgt-1", "server", "test-target", "2026-01-01T00:00:00"),
        )

    result = runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "garak_report.jsonl"),
            "--format",
            "garak",
            "--target",
            "tgt-1",
            "--db-path",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output

    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 1
        assert runs[0].target_id == "tgt-1"


def test_import_without_target_has_null_target_id(tmp_path: Path) -> None:
    """Import without --target leaves target_id NULL (backward compat)."""
    db = tmp_path / "test.db"
    result = runner.invoke(
        app,
        [
            "import",
            str(FIXTURES / "garak_report.jsonl"),
            "--format",
            "garak",
            "--db-path",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output

    with get_connection(db) as conn:
        runs = list_runs(conn, module="import")
        assert len(runs) == 1
        assert runs[0].target_id is None
