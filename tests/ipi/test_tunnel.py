"""Tests for q_ai.ipi.tunnel — adapter abstraction and Cloudflare impl."""

from __future__ import annotations

import io
import subprocess
from unittest.mock import patch

import pytest

from q_ai.ipi.tunnel import (
    CloudflareTunnelAdapter,
    TunnelBinaryNotFoundError,
    TunnelError,
    TunnelStartupError,
    get_tunnel_adapter,
)

# ---------------------------------------------------------------------------
# Helpers — a minimal fake Popen suitable for mocking subprocess.Popen
# ---------------------------------------------------------------------------


class _FakePopen:
    """Stand-in for ``subprocess.Popen`` that replays canned stderr bytes.

    ``stderr_lines`` is a list of bytes objects; each is yielded by
    ``stderr.readline`` in sequence, terminated by an EOF (``b""``).
    ``exit_code`` is what ``poll()`` returns once stderr is drained
    (simulates the subprocess having exited).
    """

    def __init__(
        self,
        stderr_lines: list[bytes] | None = None,
        exit_code: int | None = None,
    ) -> None:
        buf = b"".join(stderr_lines or [])
        self.stderr: io.BytesIO | None = io.BytesIO(buf)
        self._exit_code = exit_code
        self._terminated = False
        self._killed = False

    def poll(self) -> int | None:
        # Report running until explicitly terminated/killed or exit_code set.
        if self._terminated or self._killed:
            return 0
        return self._exit_code

    def terminate(self) -> None:
        self._terminated = True

    def kill(self) -> None:
        self._killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


# ---------------------------------------------------------------------------
# CloudflareTunnelAdapter — binary detection and install instructions
# ---------------------------------------------------------------------------


class TestCloudflareAdapterDetection:
    """Binary detection and install-instructions contract."""

    def test_provider_name_is_cloudflare(self) -> None:
        assert CloudflareTunnelAdapter().provider_name == "cloudflare"

    def test_is_available_true_when_which_finds_binary(self) -> None:
        with patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"):
            assert CloudflareTunnelAdapter().is_available() is True

    def test_is_available_false_when_which_returns_none(self) -> None:
        with patch("q_ai.ipi.tunnel.shutil.which", return_value=None):
            assert CloudflareTunnelAdapter().is_available() is False

    def test_install_instructions_mentions_every_platform(self) -> None:
        text = CloudflareTunnelAdapter().install_instructions()
        assert "brew install cloudflared" in text
        assert "snap install cloudflared" in text
        assert "pkg.cloudflare.com" in text
        assert "winget" in text or "scoop" in text
        assert "github.com/cloudflare/cloudflared" in text


# ---------------------------------------------------------------------------
# CloudflareTunnelAdapter.start — URL parsing and error paths
# ---------------------------------------------------------------------------


class TestCloudflareAdapterStart:
    """Start semantics: URL parsing, timeout, missing binary, re-start."""

    def test_start_parses_trycloudflare_url_from_stderr(self) -> None:
        stderr = [
            b"2026-04-16T12:00:01Z INF Requesting new quick Tunnel on trycloudflare.com...\n",
            b"2026-04-16T12:00:02Z INF +--------------------------------------------------+\n",
            b"2026-04-16T12:00:02Z INF |  https://bright-ocean-forest.trycloudflare.com   |\n",
            b"2026-04-16T12:00:02Z INF +--------------------------------------------------+\n",
        ]
        fake = _FakePopen(stderr_lines=stderr)

        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=fake),
        ):
            adapter = CloudflareTunnelAdapter()
            url = adapter.start(local_port=8080, timeout=5.0)
            adapter.stop()

        assert url == "https://bright-ocean-forest.trycloudflare.com"

    def test_start_raises_binary_not_found_when_missing(self) -> None:
        with patch("q_ai.ipi.tunnel.shutil.which", return_value=None):
            adapter = CloudflareTunnelAdapter()
            with pytest.raises(TunnelBinaryNotFoundError) as exc:
                adapter.start(local_port=8080)
            assert "cloudflared" in str(exc.value)

    def test_start_raises_startup_error_when_url_never_announced(self) -> None:
        # stderr closes without ever emitting a trycloudflare URL.
        stderr = [
            b"2026-04-16T12:00:01Z ERR connection failed: dial tcp: no route to host\n",
        ]
        fake = _FakePopen(stderr_lines=stderr, exit_code=1)

        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=fake),
        ):
            adapter = CloudflareTunnelAdapter()
            with pytest.raises(TunnelStartupError):
                adapter.start(local_port=8080, timeout=2.0)

    def test_start_raises_startup_error_on_timeout(self) -> None:
        # stderr yields nothing for the entire timeout window.
        class _SlowStderr:
            def readline(self) -> bytes:
                import time

                time.sleep(5.0)
                return b""

            def close(self) -> None:
                pass

        class _SlowPopen(_FakePopen):
            def __init__(self) -> None:
                super().__init__(stderr_lines=[])
                self.stderr = _SlowStderr()  # type: ignore[assignment]

        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=_SlowPopen()),
        ):
            adapter = CloudflareTunnelAdapter()
            with pytest.raises(TunnelStartupError, match="did not announce"):
                adapter.start(local_port=8080, timeout=0.2)

    def test_start_refuses_second_start_on_same_instance(self) -> None:
        stderr = [b"https://foo-bar-baz.trycloudflare.com\n"]
        fake = _FakePopen(stderr_lines=stderr)

        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=fake),
        ):
            adapter = CloudflareTunnelAdapter()
            adapter.start(local_port=8080, timeout=1.0)

            with pytest.raises(TunnelError, match="already started"):
                adapter.start(local_port=8080, timeout=1.0)

            adapter.stop()


# ---------------------------------------------------------------------------
# CloudflareTunnelAdapter.stop — subprocess lifecycle
# ---------------------------------------------------------------------------


class TestCloudflareAdapterStop:
    """Stop semantics: terminate, escalate to kill, idempotency."""

    def test_stop_before_start_is_noop(self) -> None:
        adapter = CloudflareTunnelAdapter()
        adapter.stop()  # must not raise

    def test_stop_terminates_running_process(self) -> None:
        stderr = [b"https://a-b-c.trycloudflare.com\n"]
        fake = _FakePopen(stderr_lines=stderr)

        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=fake),
        ):
            adapter = CloudflareTunnelAdapter()
            adapter.start(local_port=8080, timeout=1.0)
            adapter.stop()

        assert fake._terminated is True

    def test_stop_escalates_to_kill_when_terminate_times_out(self) -> None:
        class _StubbornFake(_FakePopen):
            """terminate() is no-op; wait() raises TimeoutExpired once."""

            def __init__(self) -> None:
                super().__init__(stderr_lines=[b"https://x-y-z.trycloudflare.com\n"])
                self._wait_count = 0

            def terminate(self) -> None:  # ignore terminate
                pass

            def wait(self, timeout: float | None = None) -> int:
                self._wait_count += 1
                if self._wait_count == 1 and timeout is not None:
                    raise subprocess.TimeoutExpired(cmd="cloudflared", timeout=timeout)
                return 0

        fake = _StubbornFake()
        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=fake),
        ):
            adapter = CloudflareTunnelAdapter()
            adapter.start(local_port=8080, timeout=1.0)
            adapter.stop()

        assert fake._killed is True

    def test_stop_is_idempotent(self) -> None:
        stderr = [b"https://q-w-e.trycloudflare.com\n"]
        fake = _FakePopen(stderr_lines=stderr)

        with (
            patch("q_ai.ipi.tunnel.shutil.which", return_value="/usr/bin/cloudflared"),
            patch("q_ai.ipi.tunnel.subprocess.Popen", return_value=fake),
        ):
            adapter = CloudflareTunnelAdapter()
            adapter.start(local_port=8080, timeout=1.0)
            adapter.stop()
            adapter.stop()  # must not raise


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetTunnelAdapter:
    """``get_tunnel_adapter`` returns correct types and rejects unknowns."""

    def test_cloudflare_returns_cloudflare_adapter(self) -> None:
        assert isinstance(get_tunnel_adapter("cloudflare"), CloudflareTunnelAdapter)

    def test_unknown_provider_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown tunnel provider"):
            get_tunnel_adapter("ngrok")
