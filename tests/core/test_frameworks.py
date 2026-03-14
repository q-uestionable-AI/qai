"""Tests for q-ai framework resolver."""
from __future__ import annotations

from q_ai.core.frameworks import FrameworkResolver


class TestFrameworkResolver:
    def test_loads_bundled_yaml(self) -> None:
        resolver = FrameworkResolver()
        frameworks = resolver.list_frameworks()
        assert len(frameworks) == 4

    def test_list_frameworks(self) -> None:
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
        assert result["mitre_atlas"] == "AML.T0040"
        assert "CWE-78" in result["cwe"]

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
        assert resolver.resolve_one("auth", "mitre_atlas") is None

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
