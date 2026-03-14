"""Tests for scanner registry."""

from __future__ import annotations

import pytest

from q_ai.audit.scanner.base import BaseScanner
from q_ai.audit.scanner.registry import get_all_scanners, get_scanner, list_scanner_names


class TestRegistry:
    def test_registry_has_10_scanners(self) -> None:
        names = list_scanner_names()
        assert len(names) == 10

    def test_all_scanner_names(self) -> None:
        names = set(list_scanner_names())
        expected = {
            "injection",
            "auth",
            "permissions",
            "tool_poisoning",
            "prompt_injection",
            "audit_telemetry",
            "context_sharing",
            "shadow_servers",
            "supply_chain",
            "token_exposure",
        }
        assert names == expected

    def test_get_scanner_returns_instance(self) -> None:
        scanner = get_scanner("injection")
        assert isinstance(scanner, BaseScanner)
        assert scanner.name == "injection"

    def test_get_scanner_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown scanner"):
            get_scanner("nonexistent")

    def test_get_all_scanners_returns_10(self) -> None:
        scanners = get_all_scanners()
        assert len(scanners) == 10

    def test_all_scanners_have_name(self) -> None:
        for scanner in get_all_scanners():
            assert scanner.name, f"Scanner {type(scanner).__name__} has no name"

    def test_all_scanners_have_category(self) -> None:
        for scanner in get_all_scanners():
            assert scanner.category, f"Scanner {scanner.name} has no category"

    def test_all_scanners_have_description(self) -> None:
        for scanner in get_all_scanners():
            assert scanner.description, f"Scanner {scanner.name} has no description"

    def test_correct_categories(self) -> None:
        expected_categories = {
            "injection": "command_injection",
            "auth": "auth",
            "token_exposure": "token_exposure",
            "permissions": "permissions",
            "tool_poisoning": "tool_poisoning",
            "prompt_injection": "prompt_injection",
            "audit_telemetry": "audit_telemetry",
            "supply_chain": "supply_chain",
            "shadow_servers": "shadow_servers",
            "context_sharing": "context_sharing",
        }
        for name, expected_cat in expected_categories.items():
            scanner = get_scanner(name)
            assert scanner.category == expected_cat, (
                f"Scanner {name} has category {scanner.category!r}, expected {expected_cat!r}"
            )

    def test_all_scanners_are_base_scanner_subclass(self) -> None:
        for scanner in get_all_scanners():
            assert isinstance(scanner, BaseScanner)

    def test_all_scanners_have_scan_method(self) -> None:
        for scanner in get_all_scanners():
            assert hasattr(scanner, "scan")
            assert callable(scanner.scan)
