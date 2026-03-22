"""Tests for inject/audit launcher controls and new API endpoints."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from q_ai.core.schema import migrate
from q_ai.server.app import create_app


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with schema applied."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def client(tmp_db: Path) -> TestClient:
    """Create a test client with a temporary database."""
    app = create_app(db_path=tmp_db)
    with TestClient(app) as c:
        yield c


class TestAssessConfigTechniques:
    """Assess config builder extracts technique and check selections."""

    def test_assess_config_includes_techniques_when_provided(self) -> None:
        from q_ai.server.routes import _build_assess_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
            "techniques": ["description_poisoning", "output_injection"],
        }
        result = _build_assess_config(body, "")
        assert result["inject"]["techniques"] == ["description_poisoning", "output_injection"]

    def test_assess_config_defaults_techniques_to_none(self) -> None:
        from q_ai.server.routes import _build_assess_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
        }
        result = _build_assess_config(body, "")
        assert result["inject"]["techniques"] is None

    def test_assess_config_includes_payload_names_when_provided(self) -> None:
        from q_ai.server.routes import _build_assess_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
            "payload_names": ["exfil_via_important_tag", "preference_manipulation"],
        }
        result = _build_assess_config(body, "")
        assert result["inject"]["payloads"] == [
            "exfil_via_important_tag",
            "preference_manipulation",
        ]

    def test_assess_config_includes_checks_when_provided(self) -> None:
        from q_ai.server.routes import _build_assess_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
            "checks": ["injection", "auth"],
        }
        result = _build_assess_config(body, "")
        assert result["audit"]["checks"] == ["injection", "auth"]

    def test_assess_config_defaults_checks_to_none(self) -> None:
        from q_ai.server.routes import _build_assess_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
        }
        result = _build_assess_config(body, "")
        assert result["audit"]["checks"] is None


class TestQuickActionConfigTechniques:
    """Quick action config builder passes techniques and checks."""

    def test_campaign_config_includes_techniques(self) -> None:
        from q_ai.server.routes import _build_quick_action_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
            "techniques": ["description_poisoning"],
        }
        config = _build_quick_action_config("campaign", body, "target-123")
        assert config["techniques"] == ["description_poisoning"]

    def test_campaign_config_defaults_techniques_to_none(self) -> None:
        from q_ai.server.routes import _build_quick_action_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "model": "openai/gpt-4",
            "rounds": 1,
        }
        config = _build_quick_action_config("campaign", body, "target-123")
        assert config.get("techniques") is None

    def test_scan_config_includes_checks(self) -> None:
        from q_ai.server.routes import _build_quick_action_config

        body = {
            "transport": "stdio",
            "command": "npx server",
            "checks": ["injection", "auth", "permissions"],
        }
        config = _build_quick_action_config("scan", body, "target-123")
        assert config["checks"] == ["injection", "auth", "permissions"]

    def test_scan_config_defaults_checks_to_none(self) -> None:
        from q_ai.server.routes import _build_quick_action_config

        body = {
            "transport": "stdio",
            "command": "npx server",
        }
        config = _build_quick_action_config("scan", body, "target-123")
        assert config.get("checks") is None


class TestInjectAdapterTechniqueFiltering:
    """Technique filtering logic works correctly."""

    def test_filters_by_single_technique(self) -> None:
        from q_ai.inject.models import InjectionTechnique, PayloadTemplate
        from q_ai.inject.payloads.loader import filter_templates

        all_templates = [
            PayloadTemplate(
                name="t1",
                technique=InjectionTechnique.DESCRIPTION_POISONING,
                description="test",
                tool_name="tool1",
                tool_description="desc1",
            ),
            PayloadTemplate(
                name="t2",
                technique=InjectionTechnique.OUTPUT_INJECTION,
                description="test",
                tool_name="tool2",
                tool_description="desc2",
            ),
        ]
        techniques = ["description_poisoning"]
        filtered = []
        for tech_str in techniques:
            tech = InjectionTechnique(tech_str)
            filtered.extend(filter_templates(all_templates, technique=tech))
        assert len(filtered) == 1
        assert filtered[0].name == "t1"

    def test_filters_by_multiple_techniques(self) -> None:
        from q_ai.inject.models import InjectionTechnique, PayloadTemplate
        from q_ai.inject.payloads.loader import filter_templates

        all_templates = [
            PayloadTemplate(
                name="t1",
                technique=InjectionTechnique.DESCRIPTION_POISONING,
                description="test",
                tool_name="tool1",
                tool_description="desc1",
            ),
            PayloadTemplate(
                name="t2",
                technique=InjectionTechnique.OUTPUT_INJECTION,
                description="test",
                tool_name="tool2",
                tool_description="desc2",
            ),
            PayloadTemplate(
                name="t3",
                technique=InjectionTechnique.CROSS_TOOL_ESCALATION,
                description="test",
                tool_name="tool3",
                tool_description="desc3",
            ),
        ]
        techniques = ["description_poisoning", "output_injection"]
        filtered = []
        seen: set[str] = set()
        for tech_str in techniques:
            tech = InjectionTechnique(tech_str)
            for t in filter_templates(all_templates, technique=tech):
                if t.name not in seen:
                    filtered.append(t)
                    seen.add(t.name)
        assert len(filtered) == 2
        assert {t.name for t in filtered} == {"t1", "t2"}


class TestPayloadLibraryEndpoint:
    """GET /api/inject/payloads returns template metadata."""

    def test_returns_payload_list(self, client: TestClient) -> None:
        """Endpoint returns all payload templates as JSON."""
        resp = client.get("/api/inject/payloads")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        first = data[0]
        assert "name" in first
        assert "technique" in first
        assert "owasp_ids" in first
        assert "description" in first

    def test_payload_entries_have_expected_fields(self, client: TestClient) -> None:
        """Each payload entry has name, technique, owasp_ids, description."""
        resp = client.get("/api/inject/payloads")
        data = resp.json()
        for entry in data:
            assert isinstance(entry["name"], str)
            assert isinstance(entry["technique"], str)
            assert isinstance(entry["owasp_ids"], list)
            assert isinstance(entry["description"], str)
