"""Tests for CXP CLI commands."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from q_ai.core.db import get_connection
from q_ai.cxp.cli import app
from q_ai.cxp.db import create_campaign, get_result, record_result


class TestFormatsCommand:
    def test_formats_lists_all(self) -> None:
        result = CliRunner().invoke(app, ["formats"])
        assert result.exit_code == 0
        assert "cursorrules" in result.output
        assert "claude-md" in result.output
        assert "copilot-instructions" in result.output
        assert "agents-md" in result.output
        assert "gemini-md" in result.output
        assert "windsurfrules" in result.output


class TestGenerateCommand:
    def test_generate_default(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["generate", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "Generated repo in" in result.output
        assert "none (clean base only)" in result.output

    def test_generate_with_rules(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "generate",
                "--rule",
                "weak-crypto-md5",
                "--rule",
                "no-csrf",
                "--output-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "weak-crypto-md5" in result.output
        assert "no-csrf" in result.output

    def test_generate_custom_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["generate", "--format", "claude-md", "--output-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        repo_dir = tmp_path / "webapp-demo-01"
        assert (repo_dir / "CLAUDE.md").is_file()

    def test_generate_custom_repo_name(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["generate", "--repo-name", "my-project", "--output-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert (tmp_path / "my-project").is_dir()

    def test_generate_creates_manifest(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["generate", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0
        manifest_path = tmp_path / "webapp-demo-01" / "manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text())
        assert "format_id" in manifest
        assert "rules_inserted" in manifest
        assert manifest["format_id"] == "cursorrules"

    def test_generate_creates_prompt_reference(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["generate", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "webapp-demo-01" / "prompt-reference.md").is_file()

    def test_generate_invalid_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["generate", "--format", "nonexistent-format", "--output-dir", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_generate_invalid_rule(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["generate", "--rule", "nonexistent-rule", "--output-dir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "Unknown rule" in result.output


class TestRecordCommand:
    def test_record_with_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        code_file = tmp_path / "auth.py"
        code_file.write_text("def login(): pass")
        result = CliRunner().invoke(
            app,
            [
                "record",
                "--technique",
                "backdoor-claude-md",
                "--assistant",
                "Claude Code",
                "--trigger-prompt",
                "Add authentication",
                "--file",
                str(code_file),
                "--db",
                str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert "Result:" in result.output
        assert "Campaign:" in result.output

    def test_record_with_output_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        output_file = tmp_path / "chat.txt"
        output_file.write_text("Here is the code...")
        result = CliRunner().invoke(
            app,
            [
                "record",
                "--technique",
                "backdoor-claude-md",
                "--assistant",
                "Claude Code",
                "--trigger-prompt",
                "Add auth",
                "--output-file",
                str(output_file),
                "--db",
                str(db_path),
            ],
        )
        assert result.exit_code == 0

    def test_record_file_and_output_file_mutually_exclusive(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        code_file = tmp_path / "auth.py"
        code_file.write_text("code")
        output_file = tmp_path / "chat.txt"
        output_file.write_text("chat")
        result = CliRunner().invoke(
            app,
            [
                "record",
                "--technique",
                "backdoor-claude-md",
                "--assistant",
                "Claude Code",
                "--trigger-prompt",
                "Add auth",
                "--file",
                str(code_file),
                "--output-file",
                str(output_file),
                "--db",
                str(db_path),
            ],
        )
        assert result.exit_code != 0

    def test_record_requires_file_or_output_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        result = CliRunner().invoke(
            app,
            [
                "record",
                "--technique",
                "backdoor-claude-md",
                "--assistant",
                "Claude Code",
                "--trigger-prompt",
                "Add auth",
                "--db",
                str(db_path),
            ],
        )
        assert result.exit_code != 0

    def test_record_invalid_technique(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        code_file = tmp_path / "auth.py"
        code_file.write_text("code")
        result = CliRunner().invoke(
            app,
            [
                "record",
                "--technique",
                "nonexistent-technique",
                "--assistant",
                "Claude Code",
                "--trigger-prompt",
                "Add auth",
                "--file",
                str(code_file),
                "--db",
                str(db_path),
            ],
        )
        assert result.exit_code != 0
        assert "Unknown technique" in result.output

    def test_record_with_existing_campaign(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            campaign = create_campaign(conn, "existing-campaign")
            campaign_id = campaign.id
        code_file = tmp_path / "auth.py"
        code_file.write_text("code")
        result = CliRunner().invoke(
            app,
            [
                "record",
                "--technique",
                "backdoor-claude-md",
                "--assistant",
                "Claude Code",
                "--trigger-prompt",
                "Add auth",
                "--file",
                str(code_file),
                "--campaign",
                campaign_id,
                "--db",
                str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert campaign_id in result.output


class TestCampaignsCommand:
    def test_campaigns_list_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path):
            pass
        result = CliRunner().invoke(app, ["campaigns", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "No campaigns" in result.output

    def test_campaigns_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            c = create_campaign(conn, "test-campaign", "A test")
            record_result(conn, c.id, "t1", "a", "p", "o", "file")
        result = CliRunner().invoke(app, ["campaigns", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "test-campaign" in result.output

    def test_campaigns_detail(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            c = create_campaign(conn, "test-campaign", "A test")
            record_result(conn, c.id, "t1", "Claude Code", "p", "o", "file")
            cid = c.id
        result = CliRunner().invoke(app, ["campaigns", cid, "--db", str(db_path)])
        assert result.exit_code == 0
        assert "test-campaign" in result.output
        assert "Claude Code" in result.output

    def test_campaigns_detail_not_found(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path):
            pass
        result = CliRunner().invoke(app, ["campaigns", "nonexistent", "--db", str(db_path)])
        assert result.exit_code != 0


class TestValidateCommand:
    def test_validate_file_hit(self, tmp_path: Path) -> None:
        code_file = tmp_path / "auth.py"
        code_file.write_text('password = "admin123"\n')
        result = CliRunner().invoke(
            app,
            ["validate", "--technique", "backdoor-claude-md", "--file", str(code_file)],
        )
        assert result.exit_code == 0
        assert "hit" in result.output.lower()

    def test_validate_file_miss(self, tmp_path: Path) -> None:
        code_file = tmp_path / "clean.py"
        code_file.write_text("def add(a, b):\n    return a + b\n")
        result = CliRunner().invoke(
            app,
            ["validate", "--technique", "backdoor-claude-md", "--file", str(code_file)],
        )
        assert result.exit_code == 0
        assert "miss" in result.output.lower()

    def test_validate_stored_result(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            campaign = create_campaign(conn, "test")
            stored = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="backdoor-claude-md",
                assistant="Claude Code",
                trigger_prompt="Add auth",
                raw_output='password = "admin123"',
                capture_mode="file",
            )
            stored_id = stored.id
        result = CliRunner().invoke(
            app,
            ["validate", "--result", stored_id, "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert "hit" in result.output.lower()
        with get_connection(db_path) as conn:
            updated = get_result(conn, stored_id)
            assert updated is not None
            assert updated.validation_result == "hit"

    def test_validate_requires_result_or_technique(self) -> None:
        result = CliRunner().invoke(app, ["validate"])
        assert result.exit_code != 0


class TestReportMatrixCommand:
    def test_matrix_markdown_stdout(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            campaign = create_campaign(conn, "test")
            record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "Add auth",
                'password = "admin123"',
                "file",
                model="sonnet",
                validation_result="hit",
            )
        result = CliRunner().invoke(app, ["report", "matrix", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "Technique" in result.output
        assert "backdoor-claude-md" in result.output

    def test_matrix_json_format(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            campaign = create_campaign(conn, "test")
            record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "p",
                "o",
                "file",
                validation_result="miss",
            )
        result = CliRunner().invoke(
            app, ["report", "matrix", "--format", "json", "--db", str(db_path)]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["total"] == 1


class TestReportPocCommand:
    def test_poc_creates_zip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            campaign = create_campaign(conn, "test")
            stored = record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "Create auth",
                'password = "admin123"',
                "file",
                model="claude-sonnet-4-20250514",
                validation_result="hit",
                validation_details="Matched backdoor-hardcoded-cred",
            )
            stored_id = stored.id
        out = tmp_path / "poc.zip"
        result = CliRunner().invoke(
            app,
            ["report", "poc", "--result", stored_id, "--output", str(out), "--db", str(db_path)],
        )
        assert result.exit_code == 0
        assert out.exists()
        assert zipfile.is_zipfile(out)

    def test_poc_pending_result_errors(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            campaign = create_campaign(conn, "test")
            stored = record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "p",
                "o",
                "file",
            )
            stored_id = stored.id
        result = CliRunner().invoke(
            app, ["report", "poc", "--result", stored_id, "--db", str(db_path)]
        )
        assert result.exit_code != 0
        assert "pending" in result.output.lower()
