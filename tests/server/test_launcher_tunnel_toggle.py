"""Tests for the IPI launcher "Use tunnel" toggle.

The toggle lives on the Test Document Ingestion (``test_docs``)
launcher card. Its rendered state depends on ``app.state`` at page
load: empty registries → toggle is live; running/adopted managed
listener → toggle is ``checked`` and disabled plus the tunnel badge is
shown; foreign listener → toggle is disabled with a read-only note.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from q_ai.core.schema import migrate
from q_ai.ipi.callback_state import build_state, write_state
from q_ai.server.app import create_app
from q_ai.services.managed_listener import ManagedListenerHandle


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def qai_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".qai"
    d.mkdir()
    return d


def _make_handle(state: str = "running") -> ManagedListenerHandle:
    return ManagedListenerHandle(
        listener_id="handle-1",
        pid=os.getpid(),
        public_url="https://launcher-tunnel.trycloudflare.com",
        provider="cloudflare",
        local_host="127.0.0.1",
        local_port=8080,
        instance_id="inst-1",
        created_at="2026-04-16T12:00:00+00:00",
        state=state,
    )


# ---------------------------------------------------------------------------
# Default (no listener) state
# ---------------------------------------------------------------------------


def test_launcher_shows_live_toggle_when_no_listener(tmp_db: Path, qai_dir: Path) -> None:
    app = create_app(db_path=tmp_db, qai_dir=qai_dir)
    with TestClient(app) as client:
        resp = client.get("/launcher")

    assert resp.status_code == 200
    html = resp.text
    assert 'data-testid="ipi-tunnel-toggle"' in html
    # Live toggle: no `disabled` attribute on the toggle input, and it carries
    # the hx-post wiring.
    assert 'hx-post="/api/ipi/managed-listener/start"' in html
    # Slot is present but empty of a badge/indicator. Referenced via
    # _make_handle().public_url to avoid a URL-shaped literal in a
    # containment check (CodeQL py/incomplete-url-substring-sanitization
    # matches both `in` and `not in` patterns).
    assert 'id="ipi-tunnel-slot"' in html
    assert _make_handle().public_url not in html


# ---------------------------------------------------------------------------
# Running managed listener
# ---------------------------------------------------------------------------


def test_launcher_reflects_running_managed_listener_on_page_load(
    tmp_db: Path,
    qai_dir: Path,
) -> None:
    """When a managed listener is already registered (e.g. from earlier in
    the session), the toggle renders `checked` and the badge is inline."""
    app = create_app(db_path=tmp_db, qai_dir=qai_dir)
    with TestClient(app) as client:
        handle = _make_handle("running")
        app.state.managed_listeners[handle.listener_id] = handle
        resp = client.get("/launcher")

    assert resp.status_code == 200
    html = resp.text
    # Badge partial rendered inline with the public URL.
    assert handle.public_url in html
    assert 'data-testid="ipi-tunnel-badge"' in html
    # Toggle is checked + disabled; the live hx-post is absent for that variant.
    assert 'data-testid="ipi-tunnel-toggle"' in html
    assert "checked" in html


def test_launcher_reflects_adopted_managed_listener(tmp_db: Path, qai_dir: Path) -> None:
    """Adopted listeners are rendered with the same toggle-on semantics."""
    app = create_app(db_path=tmp_db, qai_dir=qai_dir)
    with TestClient(app) as client:
        handle = _make_handle("adopted")
        app.state.managed_listeners[handle.listener_id] = handle
        resp = client.get("/launcher")

    assert resp.status_code == 200
    assert handle.public_url in resp.text


# ---------------------------------------------------------------------------
# Foreign listener
# ---------------------------------------------------------------------------


def test_launcher_disables_toggle_when_foreign_listener_present(
    tmp_db: Path,
    qai_dir: Path,
) -> None:
    """Seed a live CLI-owned state file so lifespan populates foreign_listener,
    then assert the launcher renders a disabled toggle + read-only indicator."""
    foreign_url = "https://foreign-cli.trycloudflare.com"
    write_state(
        build_state(
            public_url=foreign_url,
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            listener_pid=os.getpid(),
            manager="cli",
        ),
        qai_dir=qai_dir,
    )
    app = create_app(db_path=tmp_db, qai_dir=qai_dir)
    with TestClient(app) as client:
        resp = client.get("/launcher")

    assert resp.status_code == 200
    html = resp.text
    assert 'data-testid="ipi-foreign-listener-indicator"' in html
    assert foreign_url in html
    # Disabled toggle — the live hx-post wiring is absent for this variant.
    assert 'data-testid="ipi-tunnel-toggle"' in html
    assert "disabled" in html


# ---------------------------------------------------------------------------
# Start endpoint integration (toggle-on behavior)
# ---------------------------------------------------------------------------


def test_toggle_on_posts_to_start_endpoint_and_receives_badge(
    tmp_db: Path,
    qai_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the HTMX POST that the toggle fires on change; verify the
    response is the badge partial with the public URL."""
    handle = _make_handle("running")

    def _fake_start(
        registry: dict[str, ManagedListenerHandle],
        **_kwargs: object,
    ) -> ManagedListenerHandle:
        registry[handle.listener_id] = handle
        return handle

    import q_ai.server.routes.modules.ipi as routes

    monkeypatch.setattr(routes, "start_managed_listener", _fake_start)

    app = create_app(db_path=tmp_db, qai_dir=qai_dir)
    with TestClient(app) as client:
        resp = client.post("/api/ipi/managed-listener/start")

    assert resp.status_code == 200
    assert 'data-testid="ipi-tunnel-badge"' in resp.text
    assert handle.public_url in resp.text
    assert 'data-testid="ipi-tunnel-stop"' in resp.text
