"""Unit tests for ``q_ai.ipi.server._resolve_source_info``.

Covers the four branches that decide whether a hit's ``source_ip`` came
from a trusted forwarded header (tunnel mode) or the TCP peer (direct):

1. No tunnel mode set → peer host + ``via_tunnel=False``.
2. Cloudflare mode with ``CF-Connecting-IP`` header → header value +
   ``via_tunnel=True``.
3. Cloudflare mode without the header → peer host + ``via_tunnel=False``.
4. A non-Cloudflare tunnel provider (hypothetical) with the header
   present → peer host + ``via_tunnel=False`` (the forwarded header is
   Cloudflare-specific).

End-to-end coverage (e.g. the TestClient path through the listener
endpoints) lives in ``test_server_forwarded_headers.py``; this file is
the pure-function companion.
"""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace

import pytest

from q_ai.ipi import server as server_module


class _FakeHeaders:
    """Minimal stand-in for FastAPI's ``Request.headers`` used by
    :func:`_resolve_source_info` — only ``.get`` is touched.

    FastAPI's real ``Headers`` object is case-insensitive, so this fake
    normalizes both stored and looked-up keys to lowercase.
    """

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data = {k.lower(): v for k, v in (data or {}).items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key.lower(), default)


def _fake_request(
    *,
    headers: dict[str, str] | None = None,
    peer_host: str | None = "198.51.100.10",
) -> SimpleNamespace:
    """Build a duck-typed ``Request`` with only the attributes the
    resolver reads: ``headers`` and ``client``."""
    client = SimpleNamespace(host=peer_host) if peer_host is not None else None
    return SimpleNamespace(headers=_FakeHeaders(headers), client=client)


@pytest.fixture
def _clean_tunnel_mode() -> Generator[None, None, None]:
    """Ensure the module-level tunnel flag is reset after each test."""
    server_module._set_tunnel_mode(None)
    try:
        yield
    finally:
        server_module._set_tunnel_mode(None)


class TestResolveSourceInfo:
    """Direct coverage of the private resolver's four branches."""

    def test_no_tunnel_mode_returns_peer_and_direct(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """With tunnel mode unset, even a valid-looking CF-Connecting-IP
        must be ignored and ``via_tunnel=False`` returned."""
        request = _fake_request(
            headers={"CF-Connecting-IP": "203.0.113.42"},
            peer_host="198.51.100.10",
        )

        ip, via_tunnel = server_module._resolve_source_info(request)

        assert ip == "198.51.100.10"
        assert via_tunnel is False

    def test_cloudflare_tunnel_with_header_returns_header_and_tunnel(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """Cloudflare mode + CF-Connecting-IP → header value +
        ``via_tunnel=True``."""
        server_module._set_tunnel_mode("cloudflare")
        request = _fake_request(
            headers={"CF-Connecting-IP": "203.0.113.42"},
            peer_host="198.51.100.10",
        )

        ip, via_tunnel = server_module._resolve_source_info(request)

        assert ip == "203.0.113.42"
        assert via_tunnel is True

    def test_cloudflare_tunnel_without_header_falls_back_to_peer(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """Cloudflare mode but no CF-Connecting-IP header → peer host +
        ``via_tunnel=False``. Matches how a direct probe of the tunneled
        listener (bypassing Cloudflare) would land."""
        server_module._set_tunnel_mode("cloudflare")
        request = _fake_request(headers={}, peer_host="198.51.100.10")

        ip, via_tunnel = server_module._resolve_source_info(request)

        assert ip == "198.51.100.10"
        assert via_tunnel is False

    def test_non_cloudflare_provider_ignores_cf_header(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """A hypothetical non-Cloudflare provider must NOT inherit trust of
        CF-Connecting-IP — the header is Cloudflare-specific."""
        server_module._set_tunnel_mode("ngrok")
        request = _fake_request(
            headers={"CF-Connecting-IP": "203.0.113.42"},
            peer_host="198.51.100.10",
        )

        ip, via_tunnel = server_module._resolve_source_info(request)

        assert ip == "198.51.100.10"
        assert via_tunnel is False

    def test_empty_cf_header_in_cloudflare_mode_falls_back(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """An empty CF-Connecting-IP string is treated as absent — falls
        back to peer host with ``via_tunnel=False``."""
        server_module._set_tunnel_mode("cloudflare")
        request = _fake_request(
            headers={"CF-Connecting-IP": ""},
            peer_host="198.51.100.10",
        )

        ip, via_tunnel = server_module._resolve_source_info(request)

        assert ip == "198.51.100.10"
        assert via_tunnel is False

    def test_missing_client_yields_unknown(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """When the request has no ``client`` (rare but possible in FastAPI
        under some ASGI wrappers), the resolver returns ``("unknown", False)``
        rather than raising an AttributeError."""
        request = _fake_request(headers={}, peer_host=None)

        ip, via_tunnel = server_module._resolve_source_info(request)

        assert ip == server_module._UNKNOWN_IP
        assert via_tunnel is False
