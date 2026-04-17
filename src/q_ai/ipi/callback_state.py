"""Active-callback state file for cross-process callback URL discovery.

When ``qai ipi listen --tunnel`` starts a public tunnel, it writes a
structured JSON state file at ``~/.qai/active-callback``. Subsequent
invocations of ``qai ipi generate`` (in the same or a different shell)
can read that file and auto-populate the callback URL, avoiding the
need to copy/paste tunnel URLs between terminals.

The state file records the listener PID so readers can detect crashed
listeners (stale state) and ignore them with a warning. Crash recovery
relies on the next ``listen --tunnel`` overwriting the stale file.

File permissions are 0o600 on POSIX (owner-only read/write). Windows
has no direct chmod equivalent; the file is protected only by the
default user profile ACL. This is best-effort — the acceptance criteria
call it out explicitly as a known limitation.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_logger = logging.getLogger(__name__)

STATE_FILENAME = "active-callback"
"""Basename of the state file under ``~/.qai/``."""

_STATE_FILE_MODE = 0o600
"""POSIX permission bits for the state file (owner read/write only)."""

_WIN_STILL_ACTIVE = 259
"""Windows ``GetExitCodeProcess`` value indicating a live process."""

_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
"""Windows ``OpenProcess`` flag for liveness queries without elevation."""


@dataclass(frozen=True)
class CallbackState:
    """Serializable state describing an active tunneled IPI listener.

    Attributes:
        public_url: Publicly reachable HTTPS URL pointing at the
            listener (e.g. a ``trycloudflare.com`` URL).
        provider: Tunnel provider name that created ``public_url``
            (e.g. ``"cloudflare"``).
        local_host: Listener bind host on the local machine.
        local_port: Listener bind port on the local machine.
        listener_pid: PID of the qai listener process. Used by readers
            to detect stale state left by a crashed listener.
        created_at: ISO-8601 UTC timestamp recording when the state was
            written.
        instance_id: Short random identifier for this listener session
            (disambiguates between overwritten sessions for logs).
        manager: Identifies the process that wrote the state.
            ``"web-ui"`` for listeners spawned by the qai web server,
            ``"cli"`` for listeners launched from ``qai ipi listen``,
            ``None`` for state written by a pre-``manager`` build.
            Absent from JSON when ``None`` to preserve byte-for-byte
            compatibility with pre-existing CLI writers.
    """

    public_url: str
    provider: str
    local_host: str
    local_port: int
    listener_pid: int
    created_at: str
    instance_id: str
    manager: str | None = None


def state_path(qai_dir: Path | None = None) -> Path:
    """Return the filesystem path of the state file.

    Args:
        qai_dir: Override ``~/.qai`` for testing. Defaults to the real
            ``~/.qai`` directory.
    """
    base = qai_dir or Path.home() / ".qai"
    return base / STATE_FILENAME


def new_instance_id() -> str:
    """Generate a short random identifier for a listener session."""
    return secrets.token_hex(8)


def build_state(  # noqa: PLR0913 — state struct has seven independent fields; a params object would be heavier than the current callsites
    *,
    public_url: str,
    provider: str,
    local_host: str,
    local_port: int,
    listener_pid: int | None = None,
    instance_id: str | None = None,
    manager: str | None = None,
) -> CallbackState:
    """Build a :class:`CallbackState` with current-time metadata.

    Args:
        public_url: Tunnel public URL.
        provider: Tunnel provider name.
        local_host: Local bind host.
        local_port: Local bind port.
        listener_pid: PID to record. Defaults to the current process
            via :func:`os.getpid`.
        instance_id: Instance ID to record. Defaults to a fresh random
            identifier via :func:`new_instance_id`.
        manager: Optional writer-identity tag (``"web-ui"`` or
            ``"cli"``). Omitted from serialized JSON when ``None``.

    Returns:
        A frozen :class:`CallbackState` ready to pass to
        :func:`write_state`.
    """
    return CallbackState(
        public_url=public_url,
        provider=provider,
        local_host=local_host,
        local_port=local_port,
        listener_pid=listener_pid if listener_pid is not None else os.getpid(),
        created_at=datetime.now(UTC).isoformat(),
        instance_id=instance_id or new_instance_id(),
        manager=manager,
    )


def write_state(state: CallbackState, qai_dir: Path | None = None) -> Path:
    """Write the state file atomically with restrictive permissions.

    The payload is written to a sibling temporary file and then moved
    into place via :func:`os.replace`, which is atomic on both POSIX
    and Windows for Python ≥3.3. Readers therefore observe either the
    old file or the new file — never a partial one. On POSIX the temp
    file is created with mode ``0o600`` so the final file is born
    owner-only. On Windows the file is written normally — permissions
    rely on the user profile's default ACL.

    The ``manager`` field is omitted from serialized JSON when
    ``None`` so state written by a pre-``manager`` build remains
    byte-for-byte identical.

    Args:
        state: State to serialize.
        qai_dir: Override ``~/.qai`` for testing.

    Returns:
        The filesystem path written.
    """
    path = state_path(qai_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(state)
    if data.get("manager") is None:
        data.pop("manager", None)
    payload = json.dumps(data, indent=2).encode("utf-8")

    tmp_path = path.parent / f"{STATE_FILENAME}.tmp"
    try:
        if os.name != "nt":
            fd = os.open(
                str(tmp_path),
                os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
                _STATE_FILE_MODE,
            )
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        else:
            # Windows: default ACL from user profile; no 0o600 equivalent.
            tmp_path.write_bytes(payload)
        tmp_path.replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise

    return path


def read_state(qai_dir: Path | None = None) -> CallbackState | None:
    """Read and parse the state file.

    Args:
        qai_dir: Override ``~/.qai`` for testing.

    Returns:
        A :class:`CallbackState` if the file exists and parses cleanly,
        otherwise ``None``. Malformed files are logged and treated as
        missing so a corrupted state never blocks ``generate``.
    """
    path = state_path(qai_dir)
    if not path.exists():
        return None

    try:
        return _parse_state_file(path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
        _logger.warning("Active-callback state file %s is unreadable: %s", path, err)
        return None


def _parse_state_file(path: Path) -> CallbackState:
    """Read, validate, and deserialize the state file at ``path``.

    Args:
        path: Filesystem path to the state file. Must exist.

    Returns:
        A :class:`CallbackState` constructed from the file contents.

    Raises:
        OSError: On filesystem read errors.
        json.JSONDecodeError: On malformed JSON.
        KeyError: On missing required fields.
        TypeError: On fields with the wrong Python type.
        ValueError: On fields that fail coercion (e.g. non-numeric port).
    """
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError("state file is empty")

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("state file did not contain a JSON object")

    raw_manager = data.get("manager")
    manager = str(raw_manager) if raw_manager is not None else None

    return CallbackState(
        public_url=str(data["public_url"]),
        provider=str(data["provider"]),
        local_host=str(data["local_host"]),
        local_port=int(data["local_port"]),
        listener_pid=int(data["listener_pid"]),
        created_at=str(data["created_at"]),
        instance_id=str(data["instance_id"]),
        manager=manager,
    )


def delete_state(qai_dir: Path | None = None) -> None:
    """Remove the state file if it exists. Idempotent.

    Args:
        qai_dir: Override ``~/.qai`` for testing.
    """
    path = state_path(qai_dir)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def is_pid_alive(pid: int) -> bool:
    """Return whether a process with ``pid`` is currently running.

    POSIX uses :func:`os.kill` with signal 0. Windows uses
    ``OpenProcess`` + ``GetExitCodeProcess`` via ctypes (``psutil`` is
    not a qai dependency).

    Args:
        pid: Process ID to check.

    Returns:
        ``True`` if the process is alive, ``False`` if dead / not
        found / permission denied to check.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _is_pid_alive_windows(pid)
    return _is_pid_alive_posix(pid)


def _is_pid_alive_posix(pid: int) -> bool:
    """POSIX PID liveness via signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else — treat as alive.
        return True
    except OSError:
        return False
    return True


def _is_pid_alive_windows(pid: int) -> bool:
    """Windows PID liveness via ctypes OpenProcess + GetExitCodeProcess."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(
        _WIN_PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return False
        return exit_code.value == _WIN_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def read_valid_state(
    qai_dir: Path | None = None,
) -> tuple[CallbackState | None, str | None]:
    """Read the state file and validate the listener PID is alive.

    Args:
        qai_dir: Override ``~/.qai`` for testing.

    Returns:
        Tuple of ``(state, warning)``:

        - ``(state, None)`` — a valid, live state is available.
        - ``(None, warning_message)`` — the state is stale, malformed,
          or missing; ``warning_message`` is suitable for user display.
          ``warning_message`` is ``None`` when no state file exists
          (not an error condition).
    """
    state = read_state(qai_dir)
    if state is None:
        path = state_path(qai_dir)
        if path.exists():
            return None, f"Active-callback state file at {path} is unreadable; ignoring."
        return None, None

    if not is_pid_alive(state.listener_pid):
        return None, (
            f"Active-callback state references dead PID {state.listener_pid} "
            f"(listener appears to have crashed); ignoring."
        )

    return state, None


__all__ = [
    "STATE_FILENAME",
    "CallbackState",
    "build_state",
    "delete_state",
    "is_pid_alive",
    "new_instance_id",
    "read_state",
    "read_valid_state",
    "state_path",
    "write_state",
]
