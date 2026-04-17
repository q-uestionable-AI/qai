"""Managed listener service — orchestrates ``qai ipi listen --tunnel`` as a subprocess.

The Web UI's "Use tunnel" launcher toggle and the Test Document Ingestion
workflow both start a tunneled IPI callback listener without a separate
terminal. This module is the shared service-layer function pair they consume.

Lifecycle rules (locked in ``RFC/RFC-IPI-Web-UI-Managed-Listener.md``):

- **Single-listener invariant.** At most one tunneled listener (managed or
  CLI-owned) may exist at a time. Conflicts raise
  :class:`ManagedListenerConflictError`.
- **No auto-restart.** Subprocess crashes surface to the UI; the user
  restarts if they want to.
- **Adopted listeners.** After a server restart, ``_lifespan`` may
  re-register a still-live managed listener from the active-callback
  state file. Those handles are tagged ``state="adopted"``; the server
  did not spawn the process so stdout/stderr are unavailable and crash
  detection relies on periodic PID liveness polling.

Writers of the active-callback file set ``manager="web-ui"`` for
managed listeners and ``manager="cli"`` for CLI-launched ones so the
server can distinguish its own from foreign listeners.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from q_ai.ipi.callback_state import (
    CallbackState,
    delete_state,
    is_pid_alive,
    read_valid_state,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants (exported as module-level so tests and docs can reference)
# ---------------------------------------------------------------------------

_STATE_WAIT_TIMEOUT_SECS: float = 45.0
"""Ceiling on the wait for the subprocess to publish its active-callback
state. Cloudflare Quick Tunnel negotiation alone can take 20-30s; 45s
gives headroom before declaring the startup failed."""

_STATE_POLL_INTERVAL_SECS: float = 0.25
"""Interval between state-file checks during startup. Chosen to balance
startup latency against CPU noise from polling."""

_STOP_GRACE_SECS: float = 2.0
"""Time granted after SIGTERM before escalating to SIGKILL. Windows'
``Popen.terminate`` maps to ``TerminateProcess`` (no graceful signal),
so this grace window effectively applies to POSIX only; it is kept
uniform for code-path simplicity."""

_STOP_HARD_CEILING_SECS: float = 5.0
"""Total wait before declaring the subprocess stuck. If the PID is
still alive after this, the handle remains ``"stopping"`` and a
:class:`ManagedListenerStuckStopError` is raised."""

_STDERR_RING_LINES: int = 20
"""Lines of stderr retained in the per-listener ring buffer for UI
display. Matches the RFC failure-UX wording ("last 20 lines")."""

MANAGER_WEB_UI: str = "web-ui"
"""Value written into ``CallbackState.manager`` by managed listeners."""

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class ManagedListenerConflictError(Exception):
    """Raised when another tunneled listener is already active.

    Detail wording follows the RFC's locked conflict message so both
    CLI and HTTP callers surface the same text.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ManagedListenerStartupError(Exception):
    """Raised when the subprocess fails to spawn or never publishes its
    active-callback state within the timeout."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ManagedListenerStuckStopError(Exception):
    """Raised when SIGTERM + SIGKILL both fail to reap the subprocess
    within the hard ceiling."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------------------
# Handle dataclass
# ---------------------------------------------------------------------------


@dataclass
class ManagedListenerHandle:
    """In-memory record describing a single managed listener.

    The handle is mutable: its ``state`` and ``exit_code`` fields advance
    over the lifecycle. For subprocess-backed listeners (``running`` →
    ``stopping`` / ``crashed``), ``_popen`` and ``_stderr_ring`` carry
    private references used by the drain thread and by
    :func:`stop_managed_listener`. Adopted listeners leave both ``None``.

    Attributes:
        listener_id: Short random identifier unique per managed listener.
        pid: Process ID of the listener (the ``python -m q_ai ipi listen``
            subprocess, not its cloudflared child).
        public_url: Publicly-reachable HTTPS URL pointing at the listener.
        provider: Tunnel provider (e.g. ``"cloudflare"``).
        local_host: Listener bind host.
        local_port: Listener bind port.
        instance_id: Short random id from the listener's own state file.
        created_at: ISO-8601 UTC timestamp when this handle was created.
        state: One of ``"running"``, ``"adopted"``, ``"stopping"``,
            ``"crashed"``.
        exit_code: Set when ``state == "crashed"``. ``None`` otherwise, or
            for adopted listeners whose exit code cannot be observed.
    """

    listener_id: str
    pid: int
    public_url: str
    provider: str
    local_host: str
    local_port: int
    instance_id: str
    created_at: str
    state: str
    exit_code: int | None = None

    # ---- Internal: populated only for listeners this process spawned. ----
    _popen: subprocess.Popen[bytes] | None = field(default=None, repr=False, compare=False)
    _stderr_ring: collections.deque[str] | None = field(default=None, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def stderr_tail(self) -> tuple[str, ...] | None:
        """Snapshot of the most-recent captured stderr lines.

        Returns ``None`` for adopted listeners (server did not spawn the
        subprocess and therefore cannot read its stderr). For running or
        crashed subprocess-backed listeners, returns a tuple containing
        up to :data:`_STDERR_RING_LINES` most recent lines.
        """
        ring = self._stderr_ring
        if ring is None:
            return None
        return tuple(ring)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_managed_listener(
    registry: dict[str, ManagedListenerHandle],
    *,
    provider: str = "cloudflare",
    host: str = "127.0.0.1",
    port: int = 8080,
    qai_dir: Path | None = None,
) -> ManagedListenerHandle:
    """Spawn ``qai ipi listen --tunnel <provider>`` as a subprocess.

    The subprocess inherits the current process environment (RFC
    Decision 3 Option A — bridge-token auth propagates via the existing
    ``~/.qai/bridge.token`` file, not env). The command is invoked as
    ``[sys.executable, "-m", "q_ai", ...]`` so it works under editable,
    pipx, and site-packages installs without requiring ``qai`` on PATH.

    Blocks until the subprocess writes its active-callback state file
    (or the timeout fires, in which case the subprocess is terminated
    and :class:`ManagedListenerStartupError` is raised).

    Args:
        registry: The live managed-listener registry (typically
            ``app.state.managed_listeners``). Mutated in place on
            success.
        provider: Tunnel provider name passed to ``--tunnel``.
        host: Listener bind host.
        port: Listener bind port.
        qai_dir: Override ``~/.qai`` for testing.

    Returns:
        The newly-registered :class:`ManagedListenerHandle`.

    Raises:
        ManagedListenerConflictError: Another tunneled listener
            (managed or foreign) is already active.
        ManagedListenerStartupError: The subprocess failed to spawn or
            did not publish its state within the timeout.
    """
    _raise_if_conflict(registry, qai_dir)

    listener_id = _new_listener_id()
    cmd = [
        sys.executable,
        "-m",
        "q_ai",
        "ipi",
        "listen",
        "--tunnel",
        provider,
        "--host",
        host,
        "--port",
        str(port),
    ]

    try:
        proc = subprocess.Popen(  # noqa: S603 — cmd is built from sys.executable + our own constants
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as err:
        raise ManagedListenerStartupError(f"Failed to spawn listener subprocess: {err}") from err

    stderr_ring: collections.deque[str] = collections.deque(maxlen=_STDERR_RING_LINES)

    _start_stream_drain_threads(proc, stderr_ring, registry, listener_id)

    state = _wait_for_state_file(
        proc,
        qai_dir=qai_dir,
        timeout=_STATE_WAIT_TIMEOUT_SECS,
        interval=_STATE_POLL_INTERVAL_SECS,
    )
    if state is None:
        # Capture whether the subprocess was already dead before we terminate
        # it — that distinguishes "subprocess crashed early" from
        # "subprocess stuck alive past timeout".
        exit_code_before_terminate = proc.poll()
        _terminate_on_startup_failure(proc)
        if exit_code_before_terminate is not None:
            detail = (
                f"Listener subprocess exited with code "
                f"{exit_code_before_terminate} before publishing its "
                "callback state."
            )
        else:
            detail = (
                "Listener did not publish its callback state within "
                f"{_STATE_WAIT_TIMEOUT_SECS:.0f}s; subprocess terminated."
            )
        raise ManagedListenerStartupError(detail)

    handle = ManagedListenerHandle(
        listener_id=listener_id,
        pid=proc.pid,
        public_url=state.public_url,
        provider=state.provider,
        local_host=state.local_host,
        local_port=state.local_port,
        instance_id=state.instance_id,
        created_at=datetime.now(UTC).isoformat(),
        state="running",
    )
    handle._popen = proc
    handle._stderr_ring = stderr_ring
    registry[listener_id] = handle
    logger.info(
        "Managed listener %s started (pid=%d, url=%s)",
        listener_id,
        proc.pid,
        state.public_url,
    )
    return handle


def stop_managed_listener(
    registry: dict[str, ManagedListenerHandle],
    listener_id: str,
    *,
    qai_dir: Path | None = None,
) -> None:
    """Terminate a managed listener and remove it from the registry.

    Uses the SIGTERM → grace-wait → SIGKILL escalation ladder. Only
    deletes the active-callback state file if it belongs to the web-ui
    manager; foreign state files are never touched. Idempotent:
    stopping an unknown ``listener_id`` or an already-stopped handle
    is a no-op.

    Args:
        registry: The live managed-listener registry.
        listener_id: The ID of the listener to stop.
        qai_dir: Override ``~/.qai`` for testing.

    Raises:
        ManagedListenerStuckStopError: SIGTERM + SIGKILL both failed
            to reap the subprocess within
            :data:`_STOP_HARD_CEILING_SECS`. The handle remains in
            ``"stopping"`` state for the user to address.
    """
    handle = registry.get(listener_id)
    if handle is None:
        return

    with handle._lock:
        handle.state = "stopping"

    if handle._popen is not None:
        _stop_subprocess_backed(handle)
    else:
        _stop_adopted(handle)

    _maybe_delete_state_file(qai_dir)
    registry.pop(listener_id, None)
    logger.info("Managed listener %s stopped", listener_id)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _new_listener_id() -> str:
    """Short random identifier for a managed listener."""
    return secrets.token_hex(6)


def _raise_if_conflict(
    registry: dict[str, ManagedListenerHandle],
    qai_dir: Path | None,
) -> None:
    """Raise :class:`ManagedListenerConflictError` if a listener is
    already active (managed or foreign)."""
    for handle in registry.values():
        if handle.state in ("running", "adopted", "stopping") and is_pid_alive(handle.pid):
            raise ManagedListenerConflictError(
                f"A tunneled listener is already active (PID {handle.pid}, "
                f"started via {MANAGER_WEB_UI}). "
                "Stop it first or use the CLI for a parallel tunnel."
            )

    state, _warning = read_valid_state(qai_dir=qai_dir)
    if state is not None:
        manager_label = state.manager or "cli"
        raise ManagedListenerConflictError(
            f"A tunneled listener is already active (PID {state.listener_pid}, "
            f"started via {manager_label}). "
            "Stop it first or use the CLI for a parallel tunnel."
        )


def _start_stream_drain_threads(
    proc: subprocess.Popen[bytes],
    stderr_ring: collections.deque[str],
    registry: dict[str, ManagedListenerHandle],
    listener_id: str,
) -> None:
    """Spawn daemon threads that drain stdout and stderr pipes.

    Unread pipes fill their kernel buffers and eventually block the
    subprocess — cross-platform pitfall. These threads keep the pipes
    flowing and capture stderr into the bounded ring buffer for UI
    display. A post-drain ``wait()`` on each thread updates the handle
    to ``crashed`` if the subprocess died while still marked running.
    """

    def _drain(stream: object, *, capture: bool) -> None:
        try:
            readline = stream.readline  # type: ignore[attr-defined]
            while True:
                raw = readline()
                if not raw:
                    break
                if capture:
                    try:
                        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    except (UnicodeError, AttributeError):
                        continue
                    stderr_ring.append(line)
        except (ValueError, OSError):
            # Pipe closed mid-read — treat as EOF.
            pass
        finally:
            _mark_crashed_if_dead(proc, registry, listener_id)

    if proc.stderr is not None:
        t = threading.Thread(
            target=_drain,
            args=(proc.stderr,),
            kwargs={"capture": True},
            name=f"managed-listener-stderr-{listener_id}",
            daemon=True,
        )
        t.start()
    if proc.stdout is not None:
        t = threading.Thread(
            target=_drain,
            args=(proc.stdout,),
            kwargs={"capture": False},
            name=f"managed-listener-stdout-{listener_id}",
            daemon=True,
        )
        t.start()


def _mark_crashed_if_dead(
    proc: subprocess.Popen[bytes],
    registry: dict[str, ManagedListenerHandle],
    listener_id: str,
) -> None:
    """If the subprocess has exited and the handle is still ``running``,
    flip it to ``crashed`` with the exit code."""
    exit_code = proc.poll()
    if exit_code is None:
        return
    handle = registry.get(listener_id)
    if handle is None:
        return
    with handle._lock:
        if handle.state == "running":
            handle.state = "crashed"
            handle.exit_code = exit_code
            logger.warning(
                "Managed listener %s crashed (exit_code=%d)",
                listener_id,
                exit_code,
            )


def _wait_for_state_file(
    proc: subprocess.Popen[bytes],
    *,
    qai_dir: Path | None,
    timeout: float,
    interval: float,
) -> CallbackState | None:
    """Poll the active-callback file until it advertises ``proc.pid``.

    Returns the parsed state on success, or ``None`` if the timeout
    elapses or the subprocess exits before publishing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return None
        state, _warning = read_valid_state(qai_dir=qai_dir)
        if state is not None and state.listener_pid == proc.pid:
            return state
        time.sleep(interval)
    return None


def _terminate_on_startup_failure(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of a subprocess that never became healthy."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except (OSError, ProcessLookupError):
        return
    try:
        proc.wait(timeout=_STOP_GRACE_SECS)
    except subprocess.TimeoutExpired:
        pass
    else:
        return
    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        return
    try:
        proc.wait(timeout=_STOP_HARD_CEILING_SECS - _STOP_GRACE_SECS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Startup-failure terminate of pid %d did not reap within ceiling; leaving to OS",
            proc.pid,
        )


def _stop_subprocess_backed(handle: ManagedListenerHandle) -> None:
    """SIGTERM → wait → SIGKILL → wait escalation for our own Popen."""
    proc = handle._popen
    if proc is None:  # defensive; caller already checked
        return

    if proc.poll() is not None:
        return

    # Windows: terminate() maps to TerminateProcess (no graceful signal).
    # POSIX: sends SIGTERM. Same call for cross-platform uniformity.
    try:
        proc.terminate()
    except (OSError, ProcessLookupError):
        return

    try:
        proc.wait(timeout=_STOP_GRACE_SECS)
    except subprocess.TimeoutExpired:
        pass
    else:
        return

    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        return

    try:
        proc.wait(timeout=_STOP_HARD_CEILING_SECS - _STOP_GRACE_SECS)
    except subprocess.TimeoutExpired as err:
        raise ManagedListenerStuckStopError(
            f"Failed to stop listener (PID {handle.pid}); manual termination may be required"
        ) from err


def _stop_adopted(handle: ManagedListenerHandle) -> None:
    """SIGTERM-by-PID escalation for an adopted (not-our-Popen) listener."""
    if not is_pid_alive(handle.pid):
        return

    _send_signal(handle.pid, signal.SIGTERM)
    if _wait_for_death(handle.pid, _STOP_GRACE_SECS):
        return

    hard_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    _send_signal(handle.pid, hard_signal)
    if _wait_for_death(handle.pid, _STOP_HARD_CEILING_SECS - _STOP_GRACE_SECS):
        return

    raise ManagedListenerStuckStopError(
        f"Failed to stop listener (PID {handle.pid}); manual termination may be required"
    )


def _send_signal(pid: int, sig: int) -> None:
    """Best-effort :func:`os.kill`. Swallows 'already-gone' errors so
    callers can stay on a simple linear path."""
    with contextlib.suppress(OSError, ProcessLookupError):
        os.kill(pid, sig)


def _wait_for_death(pid: int, timeout: float) -> bool:
    """Poll :func:`is_pid_alive` until ``pid`` is reported dead or the
    timeout elapses. Returns ``True`` if the PID is dead by the end."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.1)
    return not is_pid_alive(pid)


def _maybe_delete_state_file(qai_dir: Path | None) -> None:
    """Delete the active-callback state file only if it is web-ui-owned."""
    state, _warning = read_valid_state(qai_dir=qai_dir)
    if state is None:
        # No live state file (or already gone / foreign-dead) — nothing to do.
        return
    if state.manager == MANAGER_WEB_UI:
        delete_state(qai_dir=qai_dir)


__all__ = [
    "MANAGER_WEB_UI",
    "ManagedListenerConflictError",
    "ManagedListenerHandle",
    "ManagedListenerStartupError",
    "ManagedListenerStuckStopError",
    "start_managed_listener",
    "stop_managed_listener",
]
