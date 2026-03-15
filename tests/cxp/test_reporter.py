"""Tests for the CXP reporter module."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.cxp.db import create_campaign, record_result
from q_ai.cxp.reporter import (
    _build_cxp_interpret_prompt,
    export_poc,
    generate_matrix,
    matrix_to_json,
    matrix_to_markdown,
)


class TestBuildCxpInterpretPrompt:
    def test_prompt_with_results(self) -> None:
        matrix = {
            "summary": {"total": 3, "hits": 2, "misses": 1, "partial": 0, "pending": 0},
            "matrix": [
                {
                    "technique_id": "backdoor-claude-md",
                    "results": [
                        {"assistant": "Claude Code"},
                        {"assistant": "Cursor"},
                    ],
                },
                {
                    "technique_id": "exfil-cursorrules",
                    "results": [{"assistant": "Claude Code"}],
                },
            ],
        }
        prompt = _build_cxp_interpret_prompt(matrix)
        assert "2 context poisoning techniques" in prompt
        assert "Claude Code, Cursor" in prompt
        assert "3 total runs" in prompt
        assert "2 objective achievements" in prompt
        assert "1 miss" in prompt

    def test_prompt_empty(self) -> None:
        matrix = {
            "summary": {"total": 0, "hits": 0, "misses": 0, "partial": 0, "pending": 0},
            "matrix": [],
        }
        prompt = _build_cxp_interpret_prompt(matrix)
        assert "No results recorded" in prompt
        assert "0 context poisoning techniques" in prompt


class TestGenerateMatrix:
    def test_generate_matrix_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            matrix = generate_matrix(conn)
            assert matrix["campaign"] == "all"
            assert matrix["summary"]["total"] == 0
            assert matrix["matrix"] == []

    def test_generate_matrix_with_results(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-campaign")
            record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="backdoor-claude-md",
                assistant="Claude Code",
                trigger_prompt="Add auth",
                raw_output='password = "admin123"',
                capture_mode="file",
                model="claude-sonnet-4-20250514",
                validation_result="hit",
                validation_details="Matched backdoor-hardcoded-cred",
            )
            record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="exfil-cursorrules",
                assistant="Cursor",
                trigger_prompt="Set up config",
                raw_output="def add(a, b): return a + b",
                capture_mode="output",
                model="gpt-4o",
                validation_result="miss",
                validation_details="No rules matched",
            )
            matrix = generate_matrix(conn)
            assert matrix["summary"]["total"] == 2
            assert matrix["summary"]["hits"] == 1
            assert matrix["summary"]["misses"] == 1
            assert len(matrix["matrix"]) == 2

    def test_matrix_campaign_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            c1 = create_campaign(conn, "campaign-1")
            c2 = create_campaign(conn, "campaign-2")
            record_result(
                conn,
                c1.id,
                "backdoor-claude-md",
                "Claude Code",
                "p",
                "o",
                "file",
                validation_result="hit",
            )
            record_result(
                conn,
                c2.id,
                "exfil-cursorrules",
                "Cursor",
                "p",
                "o",
                "file",
                validation_result="miss",
            )
            matrix = generate_matrix(conn, campaign_id=c1.id)
            assert matrix["campaign"] == c1.id
            assert matrix["summary"]["total"] == 1
            assert matrix["summary"]["hits"] == 1


class TestMatrixRendering:
    def test_matrix_to_markdown_format(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test")
            record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "Add auth",
                'password = "admin123"',
                "file",
                model="claude-sonnet-4-20250514",
                validation_result="hit",
                validation_details="Matched",
            )
            matrix = generate_matrix(conn)
        md = matrix_to_markdown(matrix)
        assert "Technique" in md
        assert "backdoor-claude-md" in md
        assert "hit" in md
        assert "Total: 1" in md

    def test_matrix_to_json_valid(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test")
            record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "p",
                "o",
                "file",
                validation_result="hit",
            )
            matrix = generate_matrix(conn)
        output = matrix_to_json(matrix)
        parsed = json.loads(output)
        assert parsed["summary"]["total"] == 1


class TestExportPoc:
    def test_export_poc_creates_zip(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-poc")
            result = record_result(
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
            output = tmp_path / "poc.zip"
            created = export_poc(conn, result.id, output)
            assert created == output
            assert output.exists()
            assert zipfile.is_zipfile(output)

    def test_export_poc_contents(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-poc")
            result = record_result(
                conn,
                campaign.id,
                "backdoor-claude-md",
                "Claude Code",
                "Create auth",
                'password = "admin123"',
                "file",
                model="sonnet",
                validation_result="hit",
                validation_details="Matched",
            )
            output = tmp_path / "poc.zip"
            export_poc(conn, result.id, output)
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            prefix = "poc-backdoor-claude-md/"
            assert any(n == f"{prefix}README.md" for n in names)
            assert any(n.startswith(f"{prefix}evidence/") for n in names)
            assert any(n.startswith(f"{prefix}poisoned-repo/") for n in names)
