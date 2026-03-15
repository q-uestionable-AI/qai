"""Tests for CXP data models."""

from __future__ import annotations

from datetime import UTC, datetime

from q_ai.cxp.models import (
    AssistantFormat,
    Campaign,
    Objective,
    Technique,
    TestResult,
    ValidationResult,
    ValidatorRule,
)


class TestObjective:
    def test_create_objective(self) -> None:
        obj = Objective(
            id="backdoor",
            name="Backdoor Insertion",
            description="Insert hidden access into generated code",
            validators=["backdoor-hardcoded-cred", "backdoor-bypass-condition"],
        )
        assert obj.id == "backdoor"
        assert obj.name == "Backdoor Insertion"
        assert obj.description == "Insert hidden access into generated code"
        assert obj.validators == ["backdoor-hardcoded-cred", "backdoor-bypass-condition"]

    def test_objective_validators_is_list(self) -> None:
        obj = Objective(id="test", name="Test", description="Test", validators=[])
        assert isinstance(obj.validators, list)


class TestAssistantFormat:
    def test_create_format(self) -> None:
        fmt = AssistantFormat(
            id="claude-md",
            filename="CLAUDE.md",
            assistant="Claude Code",
            syntax="markdown",
        )
        assert fmt.id == "claude-md"
        assert fmt.filename == "CLAUDE.md"
        assert fmt.assistant == "Claude Code"
        assert fmt.syntax == "markdown"


class TestTechnique:
    def test_create_technique(self) -> None:
        obj = Objective(
            id="backdoor",
            name="Backdoor Insertion",
            description="Test",
            validators=["v1"],
        )
        fmt = AssistantFormat(
            id="claude-md",
            filename="CLAUDE.md",
            assistant="Claude Code",
            syntax="markdown",
        )
        tech = Technique(
            id="backdoor-claude-md",
            objective=obj,
            format=fmt,
            template="TODO: template",
            trigger_prompt="TODO: trigger prompt",
            project_type="python",
        )
        assert tech.id == "backdoor-claude-md"
        assert tech.objective is obj
        assert tech.format is fmt
        assert tech.template == "TODO: template"
        assert tech.trigger_prompt == "TODO: trigger prompt"
        assert tech.project_type == "python"


class TestTestResult:
    def test_create_test_result(self) -> None:
        now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = TestResult(
            id="abc-123",
            campaign_id="camp-1",
            technique_id="backdoor-claude-md",
            assistant="Claude Code",
            model="claude-sonnet-4-6",
            timestamp=now,
            trigger_prompt="Write a login function",
            capture_mode="file",
            captured_files=["app.py"],
            raw_output="def login(): pass",
            validation_result="hit",
            validation_details="Found hardcoded credential",
            notes="Clear hit",
        )
        assert result.id == "abc-123"
        assert result.campaign_id == "camp-1"
        assert result.timestamp == now
        assert result.capture_mode == "file"
        assert result.captured_files == ["app.py"]
        assert result.validation_result == "hit"


class TestCampaign:
    def test_create_campaign(self) -> None:
        now = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        campaign = Campaign(
            id="camp-1",
            name="2026-01-15-test",
            created=now,
            description="Test campaign",
        )
        assert campaign.id == "camp-1"
        assert campaign.name == "2026-01-15-test"
        assert campaign.created == now
        assert campaign.description == "Test campaign"


class TestValidatorRule:
    def test_create_validator_rule(self) -> None:
        rule = ValidatorRule(
            id="backdoor-hardcoded-cred",
            objective_id="backdoor",
            name="Hardcoded credentials",
            description="Detects hardcoded credentials in generated code",
            patterns=[r'password\s*=\s*["\'][^"\']+["\']'],
            severity="high",
        )
        assert rule.id == "backdoor-hardcoded-cred"
        assert rule.objective_id == "backdoor"
        assert rule.severity == "high"
        assert len(rule.patterns) == 1


class TestValidationResult:
    def test_create_validation_result(self) -> None:
        result = ValidationResult(
            verdict="hit",
            matched_rules=["backdoor-hardcoded-cred"],
            details="Matched 1 rule",
        )
        assert result.verdict == "hit"
        assert result.matched_rules == ["backdoor-hardcoded-cred"]
        assert result.details == "Matched 1 rule"
