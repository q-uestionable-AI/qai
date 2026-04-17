"""Tests for tunneled-listener integration in the test_docs workflow.

Covers the workflow's auto-start behavior: localhost callback URLs never
touch the managed-listener registry; non-localhost URLs reuse an
existing managed or foreign listener if one is live, otherwise spawn
one via ``start_managed_listener``. The workflow must never call
``stop_managed_listener`` (RFC Decision 1 — lifecycle is global).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from q_ai.orchestrator.workflows.test_docs import (
    _callback_url_requires_tunnel,
    _substitute_public_url,
)
from q_ai.orchestrator.workflows.test_docs import (
    test_document_ingestion as _test_document_ingestion,
)
from q_ai.services.managed_listener import (
    ForeignListenerRecord,
    ListenerState,
    ManagedListenerHandle,
    ManagedListenerStartupError,
)

_IPI_PATCH = "q_ai.orchestrator.workflows.test_docs.IPIAdapter"


def _make_runner(app_state: object | None) -> MagicMock:
    runner = MagicMock()
    runner.run_id = "run-tunnel"
    runner._db_path = None
    runner.emit_progress = AsyncMock()
    runner.complete = AsyncMock()
    runner.fail = AsyncMock()
    # Our test_docs code reads `runner.app_state`, not `_app_state`.
    runner.app_state = app_state
    return runner


def _make_app_state(
    managed: dict[str, ManagedListenerHandle] | None = None,
    foreign: ForeignListenerRecord | None = None,
    qai_dir: Path | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        managed_listeners=managed if managed is not None else {},
        foreign_listener=foreign,
        qai_dir=qai_dir,
    )


def _make_handle(
    public_url: str,
    state: ListenerState = ListenerState.RUNNING,
    *,
    pid: int | None = None,
) -> ManagedListenerHandle:
    return ManagedListenerHandle(
        listener_id="wf-reuse",
        pid=pid if pid is not None else os.getpid(),
        public_url=public_url,
        provider="cloudflare",
        local_host="127.0.0.1",
        local_port=8080,
        instance_id="inst",
        created_at="2026-04-16T12:00:00+00:00",
        state=state,
    )


def _base_config(tmp_path: Path, *, callback_url: str) -> dict:
    return {
        "target_id": "target-1",
        "callback_url": callback_url,
        "output_dir": str(tmp_path / "out"),
        "format": "pdf",
        "payload_style": "obvious",
        "payload_type": "callback",
        "base_name": "report",
        "rxp_enabled": False,
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestCallbackUrlRequiresTunnel:
    """Verify the localhost-vs-remote heuristic that gates tunnel setup
    for the Test Document Ingestion workflow."""

    def test_localhost_does_not_require_tunnel(self) -> None:
        assert _callback_url_requires_tunnel("http://localhost:8080/callback") is False
        assert _callback_url_requires_tunnel("http://127.0.0.1:8080/callback") is False
        assert _callback_url_requires_tunnel("http://[::1]:8080/callback") is False

    def test_remote_host_requires_tunnel(self) -> None:
        assert _callback_url_requires_tunnel("http://192.168.1.42:8080/callback") is True
        assert _callback_url_requires_tunnel("https://example.com/callback") is True

    def test_parse_failure_returns_false(self) -> None:
        # Garbage string: no tunnel attempted.
        assert _callback_url_requires_tunnel("") is False


class TestSubstitutePublicUrl:
    """Verify scheme+host rewriting preserves path, query, and fragment so
    the listener's callback route keeps working through the tunnel."""

    def test_replaces_scheme_and_host_preserves_path(self) -> None:
        result = _substitute_public_url(
            "http://192.168.1.5:8080/callback",
            "https://abc.trycloudflare.com",
        )
        assert result == "https://abc.trycloudflare.com/callback"

    def test_preserves_query_and_fragment(self) -> None:
        result = _substitute_public_url(
            "http://192.168.1.5:8080/callback?token=xyz#frag",
            "https://abc.trycloudflare.com",
        )
        assert result == "https://abc.trycloudflare.com/callback?token=xyz#frag"


# ---------------------------------------------------------------------------
# Workflow behavior
# ---------------------------------------------------------------------------


class TestWorkflowTunnelIntegration:
    """End-to-end workflow behavior around tunneled listeners: reuse
    rules, auto-start fallback, PID-liveness guards on the reuse paths,
    emit_progress on failure, and the never-stop invariant."""

    async def test_localhost_target_does_not_touch_registry(self, tmp_path: Path) -> None:
        state = _make_app_state()
        runner = _make_runner(state)
        config = _base_config(tmp_path, callback_url="http://localhost:8080/callback")

        start_spy = MagicMock()
        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                start_spy,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        start_spy.assert_not_called()
        # Callback URL is preserved unchanged.
        called_config = MockIPI.call_args[0][1]
        assert called_config["callback_url"] == "http://localhost:8080/callback"

    async def test_remote_target_with_existing_managed_listener_reuses_url(
        self, tmp_path: Path
    ) -> None:
        state = _make_app_state(
            managed={
                "existing": _make_handle(
                    "https://reuse.trycloudflare.com",
                    ListenerState.RUNNING,
                )
            }
        )
        runner = _make_runner(state)
        config = _base_config(tmp_path, callback_url="http://192.168.1.5:8080/callback")

        start_spy = MagicMock()
        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                start_spy,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        start_spy.assert_not_called()
        called_config = MockIPI.call_args[0][1]
        assert called_config["callback_url"] == "https://reuse.trycloudflare.com/callback"

    async def test_remote_target_with_foreign_listener_uses_its_url(self, tmp_path: Path) -> None:
        state = _make_app_state(
            foreign=ForeignListenerRecord(
                pid=os.getpid(),
                public_url="https://foreign.trycloudflare.com",
                provider="cloudflare",
                manager="cli",
            )
        )
        runner = _make_runner(state)
        config = _base_config(tmp_path, callback_url="http://192.168.1.5:8080/callback")

        start_spy = MagicMock()
        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                start_spy,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        start_spy.assert_not_called()
        called_config = MockIPI.call_args[0][1]
        assert called_config["callback_url"] == "https://foreign.trycloudflare.com/callback"

    async def test_remote_target_with_no_listener_auto_starts(self, tmp_path: Path) -> None:
        state = _make_app_state()  # empty managed + no foreign
        runner = _make_runner(state)
        config = _base_config(tmp_path, callback_url="http://192.168.1.5:8080/callback")

        new_handle = _make_handle("https://spawn.trycloudflare.com", ListenerState.RUNNING)

        def _fake_start(
            registry: dict[str, ManagedListenerHandle],
            **_kwargs: object,
        ) -> ManagedListenerHandle:
            registry[new_handle.listener_id] = new_handle
            return new_handle

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                _fake_start,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        assert new_handle.listener_id in state.managed_listeners
        called_config = MockIPI.call_args[0][1]
        assert called_config["callback_url"] == "https://spawn.trycloudflare.com/callback"

    async def test_workflow_never_imports_stop(self) -> None:
        """RFC Decision 1: the workflow must not stop a managed listener.

        Enforced here as a name-level check — the symbol is simply not in
        the module's namespace.
        """
        import q_ai.orchestrator.workflows.test_docs as mod

        assert not hasattr(mod, "stop_managed_listener")

    async def test_no_app_state_skips_tunnel_logic(self, tmp_path: Path) -> None:
        """CLI / test usage without a web app_state must not crash and must
        leave the callback URL untouched."""
        runner = _make_runner(app_state=None)
        config = _base_config(tmp_path, callback_url="http://192.168.1.5:8080/callback")

        start_spy = MagicMock()
        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                start_spy,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        start_spy.assert_not_called()
        called_config = MockIPI.call_args[0][1]
        assert called_config["callback_url"] == "http://192.168.1.5:8080/callback"

    async def test_dead_managed_handle_is_skipped_and_spawn_runs(
        self,
        tmp_path: Path,
    ) -> None:
        """A registry entry whose PID is dead must not be reused — the
        workflow falls through to the spawn path. Covers the window
        between poller scans (per-call liveness belt-and-suspenders)."""
        # Spawn + reap a subprocess to get a known-dead PID.
        import subprocess
        import sys
        import time

        dead_proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        dead_proc.wait(timeout=5)
        time.sleep(0.05)

        dead_handle = _make_handle(
            "https://dead.trycloudflare.com",
            ListenerState.RUNNING,
            pid=dead_proc.pid,
        )
        state = _make_app_state(managed={"dead": dead_handle})
        runner = _make_runner(state)
        config = _base_config(tmp_path, callback_url="http://192.168.1.5:8080/callback")

        spawned = _make_handle("https://fresh.trycloudflare.com", ListenerState.RUNNING)

        def _fake_start(
            registry: dict[str, ManagedListenerHandle],
            **_kwargs: object,
        ) -> ManagedListenerHandle:
            registry[spawned.listener_id] = spawned
            return spawned

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                _fake_start,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        called_config = MockIPI.call_args[0][1]
        # Dead handle must have been bypassed; the freshly-spawned URL wins.
        assert called_config["callback_url"] == "https://fresh.trycloudflare.com/callback"

    async def test_auto_start_failure_emits_progress_and_falls_back(
        self,
        tmp_path: Path,
    ) -> None:
        """When :func:`start_managed_listener` raises, the workflow must
        surface the failure via ``emit_progress`` so the operator sees
        why the original (non-tunneled) callback URL is being used."""
        state = _make_app_state()  # no managed, no foreign → spawn path
        runner = _make_runner(state)
        config = _base_config(tmp_path, callback_url="http://192.168.1.5:8080/callback")

        def _fake_start(
            _registry: dict[str, ManagedListenerHandle],
            **_kwargs: object,
        ) -> ManagedListenerHandle:
            raise ManagedListenerStartupError("cloudflared not on PATH")

        with (
            patch(_IPI_PATCH) as MockIPI,
            patch(
                "q_ai.orchestrator.workflows.test_docs.start_managed_listener",
                _fake_start,
            ),
        ):
            MockIPI.return_value.run = AsyncMock()
            await _test_document_ingestion(runner, config)

        # Original callback URL preserved — workflow continued despite failure.
        called_config = MockIPI.call_args[0][1]
        assert called_config["callback_url"] == "http://192.168.1.5:8080/callback"

        # Operator-visible message emitted.
        progress_messages = [call.args[1] for call in runner.emit_progress.await_args_list]
        assert any(
            "Tunnel auto-start failed" in msg and "cloudflared not on PATH" in msg
            for msg in progress_messages
        ), f"expected auto-start-failed progress event in {progress_messages!r}"
