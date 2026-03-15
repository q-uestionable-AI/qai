"""Tests for CXP database operations."""

from __future__ import annotations

from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.cxp.db import (
    create_campaign,
    get_campaign,
    get_result,
    list_campaigns,
    list_results,
    record_result,
    update_validation,
)
from q_ai.cxp.models import Campaign, TestResult


class TestCampaignCrud:
    def test_create_campaign(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-campaign", "A test campaign")
            assert isinstance(campaign, Campaign)
            assert campaign.name == "test-campaign"
            assert campaign.description == "A test campaign"
            assert len(campaign.id) == 32
            assert campaign.created is not None

    def test_get_campaign(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            created = create_campaign(conn, "test-campaign")
            fetched = get_campaign(conn, created.id)
            assert fetched is not None
            assert fetched.id == created.id
            assert fetched.name == created.name

    def test_get_campaign_not_found(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            assert get_campaign(conn, "nonexistent") is None

    def test_list_campaigns(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            create_campaign(conn, "first")
            create_campaign(conn, "second")
            campaigns = list_campaigns(conn)
            assert len(campaigns) == 2
            assert all(isinstance(c, Campaign) for c in campaigns)
            assert {c.name for c in campaigns} == {"first", "second"}


class TestResultCrud:
    def test_record_result_file_mode(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-campaign")
            result = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="backdoor-claude-md",
                assistant="Claude Code",
                trigger_prompt="Add authentication",
                raw_output="def login(): pass",
                capture_mode="file",
                captured_files=["src/auth.py"],
            )
            assert isinstance(result, TestResult)
            assert result.campaign_id == campaign.id
            assert result.technique_id == "backdoor-claude-md"
            assert result.capture_mode == "file"
            assert result.captured_files == ["src/auth.py"]
            assert result.validation_result == "pending"

    def test_record_result_output_mode(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-campaign")
            result = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="exfil-cursorrules",
                assistant="Cursor",
                trigger_prompt="Refactor this",
                raw_output="some chat output here",
                capture_mode="output",
            )
            assert result.capture_mode == "output"
            assert result.captured_files == []
            assert result.raw_output == "some chat output here"

    def test_get_result(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test-campaign")
            created = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="backdoor-claude-md",
                assistant="Claude Code",
                trigger_prompt="Add auth",
                raw_output="code here",
                capture_mode="file",
            )
            fetched = get_result(conn, created.id)
            assert fetched is not None
            assert fetched.id == created.id
            assert fetched.technique_id == "backdoor-claude-md"

    def test_list_results_by_campaign(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            c1 = create_campaign(conn, "campaign-1")
            c2 = create_campaign(conn, "campaign-2")
            record_result(conn, c1.id, "t1", "a", "p", "o", "file")
            record_result(conn, c1.id, "t2", "a", "p", "o", "file")
            record_result(conn, c2.id, "t3", "a", "p", "o", "file")
            results_c1 = list_results(conn, campaign_id=c1.id)
            results_c2 = list_results(conn, campaign_id=c2.id)
            results_all = list_results(conn)
            assert len(results_c1) == 2
            assert len(results_c2) == 1
            assert len(results_all) == 3

    def test_captured_files_round_trip(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test")
            files = ["src/auth.py", "src/utils.py", "tests/test_auth.py"]
            result = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="t1",
                assistant="a",
                trigger_prompt="p",
                raw_output="o",
                capture_mode="file",
                captured_files=files,
            )
            fetched = get_result(conn, result.id)
            assert fetched is not None
            assert fetched.captured_files == files

    def test_record_result_with_v2_columns(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test")
            result = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="t1",
                assistant="Cursor",
                trigger_prompt="test prompt",
                raw_output="output",
                capture_mode="file",
                rules_inserted="weak-crypto-md5,hardcoded-secrets",
                format_id="cursorrules",
            )
            assert result.rules_inserted == "weak-crypto-md5,hardcoded-secrets"
            assert result.format_id == "cursorrules"
            fetched = get_result(conn, result.id)
            assert fetched is not None
            assert fetched.rules_inserted == "weak-crypto-md5,hardcoded-secrets"
            assert fetched.format_id == "cursorrules"


class TestUpdateValidation:
    def test_update_validation(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            campaign = create_campaign(conn, "test")
            result = record_result(
                conn,
                campaign_id=campaign.id,
                technique_id="backdoor-claude-md",
                assistant="Claude Code",
                trigger_prompt="Add auth",
                raw_output='password = "admin123"',
                capture_mode="file",
            )
            assert result.validation_result == "pending"
            update_validation(conn, result.id, "hit", "Matched backdoor-hardcoded-cred")
            updated = get_result(conn, result.id)
            assert updated is not None
            assert updated.validation_result == "hit"
            assert updated.validation_details == "Matched backdoor-hardcoded-cred"

    def test_update_validation_not_found(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with get_connection(db) as conn:
            update_validation(conn, "nonexistent-id", "miss", "")
