"""Tests for q_ai.ipi.server forwarded-header trust model.

Verifies that ``_resolve_source_info`` honors ``CF-Connecting-IP`` only
when tunnel mode is active, and ignores forwarded headers in local-only
mode (prevents IP spoofing).
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from q_ai.ipi import server as server_module
from q_ai.ipi.models import Hit


@pytest.fixture
def _clean_tunnel_mode() -> Generator[None, None, None]:
    """Ensure the module-level tunnel flag is reset after each test."""
    server_module._set_tunnel_mode(None)
    try:
        yield
    finally:
        server_module._set_tunnel_mode(None)


@pytest.fixture
def _captured_hits() -> Generator[list[Hit], None, None]:
    """Patch ``_record_and_log_hit`` to collect hits in a list.

    Avoids writing to the real database or notifying the bridge.
    """
    captured: list[Hit] = []

    def _capture(hit: Hit) -> None:
        captured.append(hit)

    with patch.object(server_module, "_record_and_log_hit", side_effect=_capture):
        yield captured


class TestForwardedHeaderTrust:
    """source_ip resolution with and without tunnel mode."""

    def test_local_mode_ignores_cf_connecting_ip_header(
        self,
        _clean_tunnel_mode: None,
        _captured_hits: list[Hit],
    ) -> None:
        """Without tunnel mode, CF-Connecting-IP must be ignored.

        The TestClient reports ``testclient`` as the peer host. Sending
        a spoofed CF-Connecting-IP must NOT override that.
        """
        server_module._set_tunnel_mode(None)
        client = TestClient(server_module.app)

        client.get(
            "/c/abc123/token",
            headers={"CF-Connecting-IP": "203.0.113.42"},
        )

        # Background tasks run before TestClient returns.
        assert len(_captured_hits) == 1
        assert _captured_hits[0].source_ip != "203.0.113.42"

    def test_cloudflare_tunnel_mode_trusts_cf_connecting_ip(
        self,
        _clean_tunnel_mode: None,
        _captured_hits: list[Hit],
    ) -> None:
        """In cloudflare tunnel mode, CF-Connecting-IP drives source_ip."""
        server_module._set_tunnel_mode("cloudflare")
        client = TestClient(server_module.app)

        client.get(
            "/c/abc123/token",
            headers={"CF-Connecting-IP": "203.0.113.42"},
        )

        assert len(_captured_hits) == 1
        assert _captured_hits[0].source_ip == "203.0.113.42"

    def test_tunnel_mode_without_header_falls_back_to_peer(
        self,
        _clean_tunnel_mode: None,
        _captured_hits: list[Hit],
    ) -> None:
        """When the trusted header is absent, fall back to TCP peer host."""
        server_module._set_tunnel_mode("cloudflare")
        client = TestClient(server_module.app)

        client.get("/c/abc123/token")

        assert len(_captured_hits) == 1
        # TestClient reports "testclient" as the peer host.
        assert _captured_hits[0].source_ip == "testclient"

    def test_post_endpoint_also_resolves_forwarded_ip(
        self,
        _clean_tunnel_mode: None,
        _captured_hits: list[Hit],
    ) -> None:
        """POST endpoints must use the same resolution as GET."""
        server_module._set_tunnel_mode("cloudflare")
        client = TestClient(server_module.app)

        client.post(
            "/c/abc123/token",
            headers={"CF-Connecting-IP": "198.51.100.7"},
            content=b"payload=1",
        )

        assert len(_captured_hits) == 1
        assert _captured_hits[0].source_ip == "198.51.100.7"

    def test_unauthenticated_endpoint_respects_tunnel_mode(
        self,
        _clean_tunnel_mode: None,
        _captured_hits: list[Hit],
    ) -> None:
        """Unauthenticated /c/{uuid} path must honor the same trust model."""
        server_module._set_tunnel_mode("cloudflare")
        client = TestClient(server_module.app)

        client.get(
            "/c/abc123",
            headers={"CF-Connecting-IP": "192.0.2.99"},
        )

        assert len(_captured_hits) == 1
        assert _captured_hits[0].source_ip == "192.0.2.99"
