"""Tests for q-ai framework resolver."""

from __future__ import annotations

import pytest

from q_ai.core.frameworks import FrameworkResolver

ALL_CATEGORIES = [
    "command_injection",
    "tool_poisoning",
    "prompt_injection",
    "supply_chain",
    "auth",
    "token_exposure",
    "context_sharing",
    "permissions",
    "shadow_servers",
    "audit_telemetry",
]


class TestFrameworkResolver:
    def test_loads_bundled_yaml(self) -> None:
        resolver = FrameworkResolver()
        frameworks = resolver.list_frameworks()
        assert len(frameworks) == 4

    def test_list_frameworks_returns_all_four(self) -> None:
        resolver = FrameworkResolver()
        frameworks = resolver.list_frameworks()
        assert "owasp_mcp_top10" in frameworks
        assert "owasp_agentic_top10" in frameworks
        assert "mitre_atlas" in frameworks
        assert "cwe" in frameworks

    def test_resolve_command_injection(self) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve("command_injection")
        assert result["owasp_mcp_top10"] == "MCP05"
        assert result["owasp_agentic_top10"] == "ASI02"
        assert "AML.T0043" in result["mitre_atlas"]
        assert "AML.T0050" in result["mitre_atlas"]
        assert "CWE-78" in result["cwe"]

    def test_command_injection_atlas_does_not_contain_t0040(self) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve_one("command_injection", "mitre_atlas")
        assert isinstance(result, list)
        assert "AML.T0040" not in result

    def test_resolve_unknown_category(self) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve("nonexistent_category")
        assert result == {}

    def test_resolve_one(self) -> None:
        resolver = FrameworkResolver()
        assert resolver.resolve_one("command_injection", "owasp_mcp_top10") == "MCP05"

    def test_resolve_one_unknown_framework(self) -> None:
        resolver = FrameworkResolver()
        assert resolver.resolve_one("command_injection", "nonexistent") is None

    def test_resolve_one_unmapped_category(self) -> None:
        resolver = FrameworkResolver()
        assert resolver.resolve_one("nonexistent_category", "mitre_atlas") is None

    def test_list_categories(self) -> None:
        resolver = FrameworkResolver()
        categories = resolver.list_categories()
        assert "command_injection" in categories
        assert "auth" in categories
        assert "prompt_injection" in categories

    def test_cwe_returns_list(self) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve_one("command_injection", "cwe")
        assert isinstance(result, list)
        assert "CWE-78" in result
        assert "CWE-88" in result

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_all_categories_have_atlas_mapping(self, category: str) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve_one(category, "mitre_atlas")
        assert result, f"{category} has no mitre_atlas mapping"

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_all_categories_have_cwe_mapping(self, category: str) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve_one(category, "cwe")
        assert result, f"{category} has no cwe mapping"

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_all_categories_have_owasp_agentic_mapping(self, category: str) -> None:
        resolver = FrameworkResolver()
        result = resolver.resolve_one(category, "owasp_agentic_top10")
        assert result, f"{category} has no owasp_agentic_top10 mapping"
