"""Tests for q_ai.ipi.callback_state — state file read/write and PID liveness."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from q_ai.ipi.callback_state import (
    CallbackState,
    build_state,
    delete_state,
    is_pid_alive,
    read_state,
    read_valid_state,
    state_path,
    write_state,
)


def _make_state(
    *,
    public_url: str = "https://foo-bar.trycloudflare.com",
    provider: str = "cloudflare",
    local_host: str = "127.0.0.1",
    local_port: int = 8080,
    listener_pid: int | None = None,
    instance_id: str = "abc123",
    created_at: str = "2026-04-16T12:00:00+00:00",
) -> CallbackState:
    """Build a CallbackState with sensible defaults for testing."""
    return CallbackState(
        public_url=public_url,
        provider=provider,
        local_host=local_host,
        local_port=local_port,
        listener_pid=listener_pid if listener_pid is not None else os.getpid(),
        created_at=created_at,
        instance_id=instance_id,
    )


# ---------------------------------------------------------------------------
# write_state / read_state roundtrip
# ---------------------------------------------------------------------------


class TestWriteReadRoundtrip:
    """write_state produces JSON that read_state recovers faithfully."""

    def test_roundtrip_preserves_all_fields(self, tmp_path: Path) -> None:
        state = _make_state(listener_pid=12345)
        written_path = write_state(state, qai_dir=tmp_path)

        assert written_path == state_path(tmp_path)
        assert written_path.exists()

        recovered = read_state(qai_dir=tmp_path)
        assert recovered == state

    def test_write_creates_qai_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "new-qai-dir"
        assert not nested.exists()
        write_state(_make_state(), qai_dir=nested)
        assert nested.exists()

    def test_write_overwrites_existing_file(self, tmp_path: Path) -> None:
        write_state(_make_state(instance_id="first"), qai_dir=tmp_path)
        write_state(_make_state(instance_id="second"), qai_dir=tmp_path)

        recovered = read_state(qai_dir=tmp_path)
        assert recovered is not None
        assert recovered.instance_id == "second"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only permissions")
    def test_posix_file_is_owner_only(self, tmp_path: Path) -> None:
        path = write_state(_make_state(), qai_dir=tmp_path)
        # On POSIX the file mode bits must be exactly 0o600.
        assert (path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# read_state edge cases
# ---------------------------------------------------------------------------


class TestReadStateEdgeCases:
    """Missing, empty, malformed, and schema-drift handling."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_state(qai_dir=tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

        assert read_state(qai_dir=tmp_path) is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")

        assert read_state(qai_dir=tmp_path) is None

    def test_non_object_json_returns_none(self, tmp_path: Path) -> None:
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        assert read_state(qai_dir=tmp_path) is None

    def test_missing_required_field_returns_none(self, tmp_path: Path) -> None:
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"public_url": "x"}), encoding="utf-8")

        assert read_state(qai_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# delete_state
# ---------------------------------------------------------------------------


class TestDeleteState:
    """delete_state is idempotent."""

    def test_removes_existing_file(self, tmp_path: Path) -> None:
        write_state(_make_state(), qai_dir=tmp_path)
        delete_state(qai_dir=tmp_path)
        assert not state_path(tmp_path).exists()

    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        delete_state(qai_dir=tmp_path)  # must not raise


# ---------------------------------------------------------------------------
# is_pid_alive
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    """Cross-platform PID liveness check."""

    def test_own_pid_is_alive(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_nonpositive_pid_is_dead(self) -> None:
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    def test_dead_pid_is_dead(self) -> None:
        """Spawn a quick-exit subprocess, wait for exit, then check PID."""
        import subprocess
        import time

        # Use the current Python interpreter to exit immediately.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=5)
        # Give the OS a moment to reap the zombie on POSIX.
        time.sleep(0.05)
        # On POSIX the parent still holds a zombie until wait() — which we did.
        # After wait(), the PID is no longer reported alive.
        assert is_pid_alive(proc.pid) is False


# ---------------------------------------------------------------------------
# read_valid_state
# ---------------------------------------------------------------------------


class TestReadValidState:
    """read_valid_state couples file parsing with PID liveness."""

    def test_valid_state_returns_state_and_no_warning(self, tmp_path: Path) -> None:
        write_state(_make_state(listener_pid=os.getpid()), qai_dir=tmp_path)

        state, warning = read_valid_state(qai_dir=tmp_path)

        assert state is not None
        assert state.listener_pid == os.getpid()
        assert warning is None

    def test_missing_state_returns_none_with_no_warning(self, tmp_path: Path) -> None:
        state, warning = read_valid_state(qai_dir=tmp_path)
        assert state is None
        assert warning is None

    def test_stale_pid_returns_none_with_warning(self, tmp_path: Path) -> None:
        # PID 999999 is almost certainly not running.
        write_state(_make_state(listener_pid=999_999), qai_dir=tmp_path)

        state, warning = read_valid_state(qai_dir=tmp_path)
        assert state is None
        assert warning is not None
        assert "dead PID" in warning

    def test_unreadable_state_returns_none_with_warning(self, tmp_path: Path) -> None:
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{garbage", encoding="utf-8")

        state, warning = read_valid_state(qai_dir=tmp_path)
        assert state is None
        assert warning is not None
        assert "unreadable" in warning


# ---------------------------------------------------------------------------
# build_state
# ---------------------------------------------------------------------------


class TestBuildState:
    """build_state fills in metadata."""

    def test_build_state_uses_current_pid_by_default(self) -> None:
        state = build_state(
            public_url="https://x.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
        )
        assert state.listener_pid == os.getpid()

    def test_build_state_generates_instance_id(self) -> None:
        s1 = build_state(
            public_url="https://x.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
        )
        s2 = build_state(
            public_url="https://x.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
        )
        assert s1.instance_id != s2.instance_id

    def test_build_state_sets_created_at_to_utc(self) -> None:
        state = build_state(
            public_url="https://x.trycloudflare.com",
            provider="cloudflare",
            local_host="127.0.0.1",
            local_port=8080,
        )
        # ISO-8601 UTC timestamp ends in +00:00 when format is used.
        assert "+00:00" in state.created_at or state.created_at.endswith("Z")
