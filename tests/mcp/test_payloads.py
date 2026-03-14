"""Tests for injection payload generators."""

from __future__ import annotations

from q_ai.mcp.payloads.injection import CANARY, get_injection_payloads


class TestInjectionPayloads:
    def test_canary_exists(self) -> None:
        assert CANARY
        assert isinstance(CANARY, str)
        assert len(CANARY) > 10

    def test_get_injection_payloads_returns_payloads(self) -> None:
        payloads = get_injection_payloads()
        assert len(payloads) > 0
        for p in payloads:
            assert p.technique
            assert p.platform in ("unix", "windows", "any")

    def test_filter_by_platform_unix(self) -> None:
        payloads = get_injection_payloads(platform="unix")
        for p in payloads:
            assert p.platform in ("unix", "any")

    def test_filter_by_platform_windows(self) -> None:
        payloads = get_injection_payloads(platform="windows")
        for p in payloads:
            assert p.platform in ("windows", "any")

    def test_filter_by_category_shell(self) -> None:
        payloads = get_injection_payloads(categories=["shell"])
        assert len(payloads) > 0
        # Shell payloads should include canary-based tests
        canary_payloads = [p for p in payloads if p.detection_mode == "canary"]
        assert len(canary_payloads) > 0

    def test_filter_by_category_argument(self) -> None:
        payloads = get_injection_payloads(categories=["argument"])
        assert len(payloads) > 0
        techniques = {p.technique for p in payloads}
        assert "flag_injection_help" in techniques

    def test_filter_by_category_path_traversal(self) -> None:
        payloads = get_injection_payloads(categories=["path_traversal"])
        assert len(payloads) > 0
        techniques = {p.technique for p in payloads}
        assert "path_traversal_unix" in techniques

    def test_combined_platform_and_category_filter(self) -> None:
        payloads = get_injection_payloads(
            platform="unix",
            categories=["shell"],
        )
        for p in payloads:
            assert p.platform in ("unix", "any")

    def test_all_platform_returns_all(self) -> None:
        all_payloads = get_injection_payloads(platform="all")
        unix_only = get_injection_payloads(platform="unix")
        windows_only = get_injection_payloads(platform="windows")
        # all should be >= max of the two filtered sets
        assert len(all_payloads) >= len(unix_only)
        assert len(all_payloads) >= len(windows_only)
