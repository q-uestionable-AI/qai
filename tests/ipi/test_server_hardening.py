"""Tests for q_ai.ipi.server public-exposure hardening middleware.

The hardening middleware is only installed when the listener runs
behind a tunnel (``tunnel_provider`` != ``None``). These tests exercise
the middleware against isolated FastAPI instances so state does not
leak between tests and so the global listener ``app`` singleton is
not contaminated.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from q_ai.ipi import server as server_module
from q_ai.ipi.server import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    install_hardening_middleware,
)


def _build_app_with_hardening() -> FastAPI:
    """Construct a fresh FastAPI with hardening installed and an echo route."""
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> PlainTextResponse:
        body = await request.body()
        return PlainTextResponse(body.decode("utf-8", errors="replace"))

    @app.get("/ping")
    async def ping() -> PlainTextResponse:
        return PlainTextResponse("pong")

    install_hardening_middleware(app)
    return app


@pytest.fixture
def _clean_tunnel_mode() -> Generator[None, None, None]:
    """Ensure module-level tunnel flag is reset."""
    server_module._set_tunnel_mode(None)
    try:
        yield
    finally:
        server_module._set_tunnel_mode(None)


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware
# ---------------------------------------------------------------------------


class TestBodySizeLimit:
    """1 MiB cap on Content-Length."""

    def test_small_body_passes_through(self, _clean_tunnel_mode: None) -> None:
        app = _build_app_with_hardening()
        client = TestClient(app)

        resp = client.post("/echo", content=b"hello")
        assert resp.status_code == 200
        assert resp.text == "hello"

    def test_oversize_content_length_rejected_with_413(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        app = FastAPI()

        @app.post("/echo")
        async def echo(request: Request) -> PlainTextResponse:
            return PlainTextResponse(await request.body())

        # Install only the body-size middleware to avoid rate-limit interference.
        app.add_middleware(
            BodySizeLimitMiddleware,
            max_bytes=1024,  # smaller cap for easier testing
        )

        client = TestClient(app)
        # Declare a Content-Length larger than the cap; body can be smaller.
        resp = client.post(
            "/echo",
            content=b"x" * 1025,
            headers={"Content-Length": "1025"},
        )

        assert resp.status_code == 413
        assert "too large" in resp.text.lower()

    def test_malformed_content_length_header_is_treated_as_zero(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        app = FastAPI()

        @app.post("/echo")
        async def echo(request: Request) -> PlainTextResponse:
            return PlainTextResponse(await request.body())

        app.add_middleware(BodySizeLimitMiddleware, max_bytes=1024)
        client = TestClient(app)

        # Malformed Content-Length: middleware must not crash or return 413.
        resp = client.post(
            "/echo",
            content=b"short",
            headers={"Content-Length": "not-a-number"},
        )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------


class TestRateLimit:
    """Per-IP sliding-window rate limiter."""

    def test_under_limit_passes_through(self, _clean_tunnel_mode: None) -> None:
        app = FastAPI()

        @app.get("/ping")
        async def ping() -> PlainTextResponse:
            return PlainTextResponse("pong")

        app.add_middleware(RateLimitMiddleware, window_secs=60.0, max_requests=3)
        client = TestClient(app)

        for _ in range(3):
            resp = client.get("/ping")
            assert resp.status_code == 200

    def test_over_limit_returns_429(self, _clean_tunnel_mode: None) -> None:
        app = FastAPI()

        @app.get("/ping")
        async def ping() -> PlainTextResponse:
            return PlainTextResponse("pong")

        app.add_middleware(RateLimitMiddleware, window_secs=60.0, max_requests=2)
        client = TestClient(app)

        # First two pass.
        assert client.get("/ping").status_code == 200
        assert client.get("/ping").status_code == 200
        # Third is throttled.
        blocked = client.get("/ping")
        assert blocked.status_code == 429
        assert "rate limit" in blocked.text.lower()

    def test_rate_limit_uses_forwarded_ip_when_tunneled(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """Two different CF-Connecting-IPs share separate buckets in tunnel mode."""
        server_module._set_tunnel_mode("cloudflare")

        app = FastAPI()

        @app.get("/ping")
        async def ping() -> PlainTextResponse:
            return PlainTextResponse("pong")

        app.add_middleware(RateLimitMiddleware, window_secs=60.0, max_requests=1)
        client = TestClient(app)

        # IP A exhausts its single-request quota.
        assert client.get("/ping", headers={"CF-Connecting-IP": "1.1.1.1"}).status_code == 200
        blocked_a = client.get("/ping", headers={"CF-Connecting-IP": "1.1.1.1"})
        assert blocked_a.status_code == 429

        # IP B still has quota — it's a different bucket.
        assert client.get("/ping", headers={"CF-Connecting-IP": "2.2.2.2"}).status_code == 200


# ---------------------------------------------------------------------------
# install_hardening_middleware
# ---------------------------------------------------------------------------


class TestInstallHardeningMiddleware:
    """install_hardening_middleware registers both middlewares."""

    def test_adds_both_body_limit_and_rate_limit(self) -> None:
        app = FastAPI()
        install_hardening_middleware(app)

        # FastAPI stores added middlewares in user_middleware.
        middleware_classes = {mw.cls for mw in app.user_middleware}
        assert BodySizeLimitMiddleware in middleware_classes
        assert RateLimitMiddleware in middleware_classes


# ---------------------------------------------------------------------------
# Error-path hardening (RFC Decision 3)
# ---------------------------------------------------------------------------


class TestErrorPathHardening:
    """Unhandled exceptions on a tunneled listener must not leak server
    internals into response bodies.

    RFC-IPI-Web-UI-Managed-Listener Decision 3 pins FastAPI's default
    production exception behavior (generic 500 body with no detail).
    The rule is "don't override FastAPI's default exception handling" —
    a future change that registers a custom exception handler echoing
    ``str(exc)`` into a response body would fail this test.

    The assertion list intentionally looks for markers that would only
    appear if someone broke the rule: exception message contents,
    module paths (``q_ai/``), common env-derived substrings, and the
    word ``Traceback``. Keep the pattern list tight and motivated —
    false positives here would make the test flaky across unrelated
    FastAPI upgrades.
    """

    _LEAKY_EXCEPTION_MESSAGE = (
        "sensitive-env-var=SECRET_VALUE_TOKEN "
        "config_path=/home/user/.qai/qai.db "
        "File q_ai/ipi/server.py"
    )

    def _build_failing_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/boom")
        async def _boom() -> None:
            raise RuntimeError(self._LEAKY_EXCEPTION_MESSAGE)

        install_hardening_middleware(app)
        return app

    def test_unhandled_exception_returns_generic_500(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        app = self._build_failing_app()
        # raise_server_exceptions=False makes TestClient behave like
        # production — surface FastAPI's default 500 body rather than
        # re-raising the exception into the test.
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom")

        assert resp.status_code == 500
        # Default FastAPI/Starlette production body is "Internal Server Error".
        # Either that exact string or an empty body is acceptable; anything else
        # signals a regression where exception detail leaked.
        assert resp.text in ("", "Internal Server Error"), (
            "unexpected 500 body; a custom exception handler may have been "
            f"installed that echoes detail into the response: {resp.text!r}"
        )

    def test_500_body_contains_no_exception_message(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """Specifically guard against the 'echo str(exc) into the body' bug."""
        app = self._build_failing_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom")

        body = resp.text
        assert "SECRET_VALUE_TOKEN" not in body
        assert "sensitive-env-var" not in body
        assert "/home/user" not in body
        assert "qai.db" not in body

    def test_500_body_contains_no_traceback_or_module_paths(
        self,
        _clean_tunnel_mode: None,
    ) -> None:
        """Rule out framework-level tracebacks and source-tree paths."""
        app = self._build_failing_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom")

        body = resp.text
        assert "Traceback" not in body
        assert "RuntimeError" not in body
        # Both POSIX and Windows path forms.
        assert "q_ai/" not in body
        assert "q_ai\\" not in body
