"""Tests for q_ai.services.managed_listener — start/stop orchestration.

All tests use a fake subprocess that impersonates :class:`subprocess.Popen`
so the service can be exercised without spawning a real listener or a
real cloudflared child. The fake reports ``os.getpid()`` as its PID so
:func:`is_pid_alive` returns True and state-file match succeeds.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from q_ai.ipi.callback_state import (
    build_state,
    state_path,
    write_state,
)
from q_ai.services.managed_listener import (
    MANAGER_WEB_UI,
    ManagedListenerConflictError,
    ManagedListenerHandle,
    ManagedListenerStartupError,
    ManagedListenerStuckStopError,
    start_managed_listener,
    stop_managed_listener,
)

# ---------------------------------------------------------------------------
# Fake Popen
# ---------------------------------------------------------------------------


class _FakeStream:
    """Readline-only stream whose EOF is gated on a :class:`threading.Event`.

    Real subprocess stderr stays open until the process dies. For a
    :class:`io.BytesIO` the readline returns ``b""`` immediately, which
    would make the drain thread exit before the fake subprocess has a
    chance to ``force_exit``. Blocking readline on the death event
    models the real behavior.
    """

    def __init__(self, death_event: threading.Event) -> None:
        self._death = death_event

    def readline(self) -> bytes:
        # Block until the fake subprocess is declared dead, then EOF.
        self._death.wait()
        return b""

    def close(self) -> None:  # pragma: no cover — drain path doesn't call close
        pass


class _FakePopen:
    """Duck-type stand-in for :class:`subprocess.Popen`.

    Parameters control whether the subprocess is "alive", what exit code
    it reports, whether it dies on ``terminate()``/``kill()``, and
    whether (and when) it writes a matching active-callback state file.
    All behavior is in-process and deterministic.
    """

    def __init__(
        self,
        *,
        pid: int | None = None,
        qai_dir: Path | None = None,
        write_state_after: float | None = 0.05,
        initial_exit_code: int | None = None,
        terminate_kills: bool = True,
        kill_kills: bool = True,
    ) -> None:
        self.pid = pid if pid is not None else os.getpid()
        self.args: list[str] = []
        self._exit_code: int | None = initial_exit_code
        self._terminate_kills = terminate_kills
        self._kill_kills = kill_kills
        self.terminate_called = False
        self.kill_called = False
        self._death_event = threading.Event()
        if self._exit_code is not None:
            self._death_event.set()
        self.stdout = _FakeStream(self._death_event)
        self.stderr = _FakeStream(self._death_event)
        if write_state_after is not None and qai_dir is not None:
            threading.Thread(
                target=self._write_state_soon,
                args=(qai_dir, write_state_after),
                daemon=True,
            ).start()

    def _write_state_soon(self, qai_dir: Path, delay: float) -> None:
        time.sleep(delay)
        if self._exit_code is not None:
            return
        state = build_state(
            public_url="https://fake-abc.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            listener_pid=self.pid,
            manager=MANAGER_WEB_UI,
        )
        write_state(state, qai_dir=qai_dir)

    def poll(self) -> int | None:
        return self._exit_code

    def wait(self, timeout: float | None = None) -> int:
        if self._exit_code is not None:
            return self._exit_code
        if timeout is not None:
            if self._death_event.wait(timeout):
                assert self._exit_code is not None
                return self._exit_code
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        self._death_event.wait()
        assert self._exit_code is not None
        return self._exit_code

    def terminate(self) -> None:
        self.terminate_called = True
        if self._terminate_kills:
            self._exit_code = 0
            self._death_event.set()

    def kill(self) -> None:
        self.kill_called = True
        if self._kill_kills:
            self._exit_code = -9
            self._death_event.set()

    def force_exit(self, code: int) -> None:
        """Drive the fake subprocess to a dead state (used by crash tests)."""
        self._exit_code = code
        self._death_event.set()


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], _FakePopen],
) -> None:
    """Redirect ``subprocess.Popen`` in the service module to a factory
    that returns a :class:`_FakePopen`."""
    import q_ai.services.managed_listener as mod

    def _wrapped(args: list[str], **_kwargs: object) -> _FakePopen:
        proc = factory()
        proc.args = args
        return proc

    monkeypatch.setattr(mod.subprocess, "Popen", _wrapped)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestStartManagedListener:
    def test_successful_start_populates_registry_and_returns_handle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry: dict[str, ManagedListenerHandle] = {}
        _patch_popen(monkeypatch, lambda: _FakePopen(qai_dir=tmp_path))

        handle = start_managed_listener(registry, qai_dir=tmp_path)

        assert handle.listener_id in registry
        assert registry[handle.listener_id] is handle
        assert handle.state == "running"
        assert handle.pid == os.getpid()
        assert handle.public_url == "https://fake-abc.trycloudflare.com"
        assert handle.provider == "cloudflare"
        assert handle.local_host == "127.0.0.1"
        assert handle.local_port == 8080
        assert handle.exit_code is None
        assert handle.stderr_tail is not None

    def test_uses_sys_executable_with_module_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Brief constraint: spawn command must use sys.executable + `-m q_ai`
        so bare-`qai`-on-PATH is not required."""
        import sys

        captured: list[list[str]] = []

        def _factory() -> _FakePopen:
            return _FakePopen(qai_dir=tmp_path)

        import q_ai.services.managed_listener as mod

        def _wrapped(args: list[str], **_kwargs: object) -> _FakePopen:
            captured.append(args)
            proc = _factory()
            proc.args = args
            return proc

        monkeypatch.setattr(mod.subprocess, "Popen", _wrapped)

        start_managed_listener({}, qai_dir=tmp_path)

        assert len(captured) == 1
        cmd = captured[0]
        assert cmd[0] == sys.executable
        assert cmd[1:5] == ["-m", "q_ai", "ipi", "listen"]
        assert "--tunnel" in cmd
        assert "cloudflare" in cmd


# ---------------------------------------------------------------------------
# Conflict paths
# ---------------------------------------------------------------------------


class TestConflict:
    def test_conflict_with_foreign_cli_state_file(
        self,
        tmp_path: Path,
    ) -> None:
        """A CLI-owned state file with a live PID blocks managed start."""
        write_state(
            build_state(
                public_url="https://cli-foreign.trycloudflare.com",
                provider="cloudflare",
                local_host="127.0.0.1",
                local_port=8080,
                listener_pid=os.getpid(),
                manager="cli",
            ),
            qai_dir=tmp_path,
        )

        with pytest.raises(ManagedListenerConflictError) as exc:
            start_managed_listener({}, qai_dir=tmp_path)

        assert f"PID {os.getpid()}" in exc.value.detail
        assert "started via cli" in exc.value.detail
        assert "Stop it first" in exc.value.detail

    def test_conflict_with_unlabeled_foreign_state_file_uses_cli_label(
        self,
        tmp_path: Path,
    ) -> None:
        """Brief: when a foreign state file has no `manager` field (legacy CLI
        listener), the conflict message reports 'cli'."""
        write_state(
            build_state(
                public_url="https://legacy.trycloudflare.com",
                provider="cloudflare",
                local_host="127.0.0.1",
                local_port=8080,
                listener_pid=os.getpid(),
                # manager omitted — legacy CLI listener
            ),
            qai_dir=tmp_path,
        )

        with pytest.raises(ManagedListenerConflictError) as exc:
            start_managed_listener({}, qai_dir=tmp_path)

        assert "started via cli" in exc.value.detail

    def test_conflict_with_in_registry_managed_listener(
        self,
        tmp_path: Path,
    ) -> None:
        """An already-registered managed handle whose PID is alive blocks."""
        registry = {
            "abc123": ManagedListenerHandle(
                listener_id="abc123",
                pid=os.getpid(),
                public_url="https://existing.trycloudflare.com",
                provider="cloudflare",
                local_host="127.0.0.1",
                local_port=8080,
                instance_id="inst-1",
                created_at="2026-04-16T12:00:00+00:00",
                state="running",
            ),
        }

        with pytest.raises(ManagedListenerConflictError) as exc:
            start_managed_listener(registry, qai_dir=tmp_path)

        assert "started via web-ui" in exc.value.detail

    def test_no_conflict_when_registry_entry_is_crashed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Crashed entries should not block a fresh start — the user is
        expected to clear them from the UI, and a fresh start is harmless."""
        registry: dict[str, ManagedListenerHandle] = {
            "old": ManagedListenerHandle(
                listener_id="old",
                pid=os.getpid(),
                public_url="https://old.trycloudflare.com",
                provider="cloudflare",
                local_host="127.0.0.1",
                local_port=8080,
                instance_id="inst-old",
                created_at="2026-04-16T12:00:00+00:00",
                state="crashed",
                exit_code=1,
            ),
        }
        _patch_popen(monkeypatch, lambda: _FakePopen(qai_dir=tmp_path))

        handle = start_managed_listener(registry, qai_dir=tmp_path)
        assert handle.state == "running"


# ---------------------------------------------------------------------------
# Startup failure paths
# ---------------------------------------------------------------------------


class TestStartupFailure:
    def test_subprocess_exits_before_publishing_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_popen(
            monkeypatch,
            lambda: _FakePopen(
                qai_dir=tmp_path,
                write_state_after=None,
                initial_exit_code=1,
            ),
        )

        with pytest.raises(ManagedListenerStartupError) as exc:
            start_managed_listener({}, qai_dir=tmp_path)

        assert "exited with code 1" in exc.value.detail

    def test_state_file_never_appears_within_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the subprocess stays alive but never writes its state, the
        service terminates it and raises a timeout-styled error."""
        _patch_popen(
            monkeypatch,
            lambda: _FakePopen(qai_dir=tmp_path, write_state_after=None),
        )

        import q_ai.services.managed_listener as mod

        monkeypatch.setattr(mod, "_STATE_WAIT_TIMEOUT_SECS", 0.3)
        monkeypatch.setattr(mod, "_STATE_POLL_INTERVAL_SECS", 0.05)

        with pytest.raises(ManagedListenerStartupError) as exc:
            start_managed_listener({}, qai_dir=tmp_path)

        assert "did not publish" in exc.value.detail
        assert "0s" in exc.value.detail  # timeout formatted to whole seconds

    def test_popen_oserror_wraps_as_startup_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import q_ai.services.managed_listener as mod

        def _boom(*_args: object, **_kwargs: object) -> object:
            raise OSError("cloudflared not found")

        monkeypatch.setattr(mod.subprocess, "Popen", _boom)

        with pytest.raises(ManagedListenerStartupError) as exc:
            start_managed_listener({}, qai_dir=tmp_path)

        assert "Failed to spawn listener subprocess" in exc.value.detail
        assert "cloudflared not found" in exc.value.detail


# ---------------------------------------------------------------------------
# Crash detection
# ---------------------------------------------------------------------------


class TestCrashDetection:
    def test_running_listener_flips_to_crashed_when_subprocess_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakePopen(qai_dir=tmp_path)
        _patch_popen(monkeypatch, lambda: fake)

        registry: dict[str, ManagedListenerHandle] = {}
        handle = start_managed_listener(registry, qai_dir=tmp_path)
        assert handle.state == "running"

        # Drive the subprocess to a dead state. The background drain thread
        # eventually calls proc.poll() and flips the handle.
        fake.force_exit(2)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if handle.state == "crashed":
                break
            time.sleep(0.05)

        assert handle.state == "crashed"
        assert handle.exit_code == 2


# ---------------------------------------------------------------------------
# Stop paths
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_unknown_listener_id_is_noop(
        self,
        tmp_path: Path,
    ) -> None:
        registry: dict[str, ManagedListenerHandle] = {}
        stop_managed_listener(registry, "nonexistent", qai_dir=tmp_path)
        assert registry == {}

    def test_stop_successful_removes_handle_and_deletes_web_ui_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_popen(monkeypatch, lambda: _FakePopen(qai_dir=tmp_path))
        registry: dict[str, ManagedListenerHandle] = {}
        handle = start_managed_listener(registry, qai_dir=tmp_path)

        stop_managed_listener(registry, handle.listener_id, qai_dir=tmp_path)

        assert handle.listener_id not in registry
        assert not state_path(tmp_path).exists()

    def test_stop_does_not_delete_foreign_state_file(
        self,
        tmp_path: Path,
    ) -> None:
        """If the state file is foreign (manager != web-ui), stop must leave
        it alone even after terminating the managed listener's PID."""
        # Construct a managed handle with a fake Popen and a foreign state file.
        fake = _FakePopen(qai_dir=None, write_state_after=None)
        handle = ManagedListenerHandle(
            listener_id="abc",
            pid=fake.pid,
            public_url="https://dead.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="x",
            created_at="2026-04-16T12:00:00+00:00",
            state="running",
        )
        handle._popen = fake  # type: ignore[assignment]
        registry = {"abc": handle}

        # Seed a foreign state file (CLI) with a live PID.
        write_state(
            build_state(
                public_url="https://cli.trycloudflare.com",
                provider="cloudflare",
                local_host="127.0.0.1",
                local_port=8080,
                listener_pid=os.getpid(),
                manager="cli",
            ),
            qai_dir=tmp_path,
        )

        stop_managed_listener(registry, "abc", qai_dir=tmp_path)

        # Handle is removed regardless.
        assert "abc" not in registry
        # Foreign state file survives.
        assert state_path(tmp_path).exists()

    def test_stop_escalates_to_kill_when_terminate_ignored(
        self,
        tmp_path: Path,
    ) -> None:
        fake = _FakePopen(
            qai_dir=None,
            write_state_after=None,
            terminate_kills=False,
            kill_kills=True,
        )
        handle = ManagedListenerHandle(
            listener_id="esc",
            pid=fake.pid,
            public_url="https://esc.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="x",
            created_at="2026-04-16T12:00:00+00:00",
            state="running",
        )
        handle._popen = fake  # type: ignore[assignment]
        registry = {"esc": handle}

        import q_ai.services.managed_listener as mod

        # Shrink the grace period so the test runs quickly.
        # (patch via monkeypatch would be cleaner, but this test is self-contained.)
        original_grace = mod._STOP_GRACE_SECS
        original_ceiling = mod._STOP_HARD_CEILING_SECS
        mod._STOP_GRACE_SECS = 0.2  # type: ignore[misc]
        mod._STOP_HARD_CEILING_SECS = 1.0  # type: ignore[misc]
        try:
            stop_managed_listener(registry, "esc", qai_dir=tmp_path)
        finally:
            mod._STOP_GRACE_SECS = original_grace  # type: ignore[misc]
            mod._STOP_HARD_CEILING_SECS = original_ceiling  # type: ignore[misc]

        assert fake.terminate_called is True
        assert fake.kill_called is True
        assert "esc" not in registry

    def test_stop_stuck_raises_and_leaves_handle_in_stopping(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakePopen(
            qai_dir=None,
            write_state_after=None,
            terminate_kills=False,
            kill_kills=False,
        )
        handle = ManagedListenerHandle(
            listener_id="stuck",
            pid=fake.pid,
            public_url="https://stuck.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="x",
            created_at="2026-04-16T12:00:00+00:00",
            state="running",
        )
        handle._popen = fake  # type: ignore[assignment]
        registry = {"stuck": handle}

        import q_ai.services.managed_listener as mod

        monkeypatch.setattr(mod, "_STOP_GRACE_SECS", 0.1)
        monkeypatch.setattr(mod, "_STOP_HARD_CEILING_SECS", 0.3)

        with pytest.raises(ManagedListenerStuckStopError) as exc:
            stop_managed_listener(registry, "stuck", qai_dir=tmp_path)

        assert "manual termination may be required" in exc.value.detail
        # Handle stays in registry so the user can see the stuck state.
        assert "stuck" in registry
        assert handle.state == "stopping"

    def test_double_stop_is_idempotent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_popen(monkeypatch, lambda: _FakePopen(qai_dir=tmp_path))
        registry: dict[str, ManagedListenerHandle] = {}
        handle = start_managed_listener(registry, qai_dir=tmp_path)

        stop_managed_listener(registry, handle.listener_id, qai_dir=tmp_path)
        # Second call is against a now-unknown id; must not raise.
        stop_managed_listener(registry, handle.listener_id, qai_dir=tmp_path)
        assert handle.listener_id not in registry

    def test_stop_adopted_listener_uses_os_kill(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Adopted listeners have no Popen; stop must signal by PID."""
        handle = ManagedListenerHandle(
            listener_id="adopted",
            pid=os.getpid(),
            public_url="https://adopted.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="x",
            created_at="2026-04-16T12:00:00+00:00",
            state="adopted",
        )
        registry = {"adopted": handle}
        calls: list[tuple[int, int]] = []

        import q_ai.services.managed_listener as mod

        def _fake_kill(pid: int, sig: int) -> None:
            calls.append((pid, sig))

        monkeypatch.setattr(mod.os, "kill", _fake_kill)

        # Pretend the PID dies immediately after the first signal.
        liveness_calls = {"n": 0}
        real_is_alive = mod.is_pid_alive

        def _fake_is_alive(pid: int) -> bool:
            liveness_calls["n"] += 1
            # First call (pre-signal check) — alive.
            # Subsequent calls — dead.
            return liveness_calls["n"] == 1

        monkeypatch.setattr(mod, "is_pid_alive", _fake_is_alive)
        _ = real_is_alive

        stop_managed_listener(registry, "adopted", qai_dir=tmp_path)

        assert len(calls) == 1  # one SIGTERM, no escalation needed
        assert "adopted" not in registry

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only SIGKILL path")
    def test_stop_adopted_escalates_to_sigkill_on_posix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the adopted listener ignores SIGTERM, escalate with SIGKILL.

        Regression guard for the earlier bug where the fallback used
        ``getattr(signal, "SIGKILL", signal.SIGTERM)``, which silently sent
        SIGTERM twice on platforms where SIGKILL was unavailable. POSIX
        always has SIGKILL; we assert the second signal is SIGKILL, not
        another SIGTERM.
        """
        handle = ManagedListenerHandle(
            listener_id="adopted",
            pid=os.getpid(),
            public_url="https://adopted.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="x",
            created_at="2026-04-16T12:00:00+00:00",
            state="adopted",
        )
        registry = {"adopted": handle}
        signals_sent: list[int] = []

        import q_ai.services.managed_listener as mod

        def _fake_kill(pid: int, sig: int) -> None:
            signals_sent.append(sig)

        monkeypatch.setattr(mod.os, "kill", _fake_kill)

        # Liveness is driven by what has been signalled. This guarantees
        # the SIGTERM wait times out (PID stays alive) and forces
        # escalation into SIGKILL, regardless of loop-poll timing.
        def _fake_is_alive(pid: int) -> bool:
            return signal.SIGKILL not in signals_sent

        monkeypatch.setattr(mod, "is_pid_alive", _fake_is_alive)

        # Keep the wall-clock wait short so test runtime stays small.
        monkeypatch.setattr(mod, "_STOP_GRACE_SECS", 0.05)
        monkeypatch.setattr(mod, "_STOP_HARD_CEILING_SECS", 0.1)

        stop_managed_listener(registry, "adopted", qai_dir=tmp_path)

        assert signals_sent == [signal.SIGTERM, signal.SIGKILL]
        assert "adopted" not in registry

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only taskkill path")
    def test_stop_adopted_escalates_to_taskkill_on_windows(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On Windows, hard-kill of an adopted listener uses taskkill /F.

        ``os.kill`` cannot force-terminate a PID the caller does not own a
        handle for; ``taskkill /F /PID`` asks the OS to force-close by PID
        regardless of parentage. This test pins the Windows escalation path
        against the earlier bug where it silently degraded to a second
        SIGTERM.
        """
        handle = ManagedListenerHandle(
            listener_id="adopted",
            pid=os.getpid(),
            public_url="https://adopted.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="x",
            created_at="2026-04-16T12:00:00+00:00",
            state="adopted",
        )
        registry = {"adopted": handle}
        os_kill_calls: list[int] = []
        subprocess_run_argvs: list[list[str]] = []

        import q_ai.services.managed_listener as mod

        def _fake_kill(pid: int, sig: int) -> None:
            os_kill_calls.append(sig)

        def _fake_run(argv: list[str], **_kwargs: object) -> object:
            subprocess_run_argvs.append(list(argv))

            class _CompletedStub:
                returncode = 0

            return _CompletedStub()

        monkeypatch.setattr(mod.os, "kill", _fake_kill)
        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        # Liveness is driven by what has been called, not by a counter.
        # This guarantees the SIGTERM wait times out (PID stays alive) and
        # forces escalation into _hard_kill_pid — regardless of how fast
        # the wait loop polls. Once taskkill has been invoked, liveness
        # flips to dead so the post-kill wait returns quickly.
        def _fake_is_alive(pid: int) -> bool:
            return len(subprocess_run_argvs) == 0

        monkeypatch.setattr(mod, "is_pid_alive", _fake_is_alive)

        # Keep the wall-clock wait short so test runtime stays small.
        monkeypatch.setattr(mod, "_STOP_GRACE_SECS", 0.05)
        monkeypatch.setattr(mod, "_STOP_HARD_CEILING_SECS", 0.1)

        stop_managed_listener(registry, "adopted", qai_dir=tmp_path)

        # First signal: SIGTERM via os.kill.
        assert os_kill_calls == [signal.SIGTERM]
        # Escalation: exactly one taskkill invocation with /F /PID forms.
        assert len(subprocess_run_argvs) == 1
        argv = subprocess_run_argvs[0]
        assert argv[0] == "taskkill"
        assert "/F" in argv
        assert "/PID" in argv
        assert str(os.getpid()) in argv
        assert "adopted" not in registry


# ---------------------------------------------------------------------------
# Handle-level invariants
# ---------------------------------------------------------------------------


class TestHandleProperties:
    def test_stderr_tail_returns_none_for_adopted(self) -> None:
        handle = ManagedListenerHandle(
            listener_id="x",
            pid=os.getpid(),
            public_url="https://x.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
            instance_id="inst",
            created_at="2026-04-16T12:00:00+00:00",
            state="adopted",
        )
        assert handle.stderr_tail is None
