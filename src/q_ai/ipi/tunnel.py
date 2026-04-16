"""Reverse-tunnel adapters for exposing the IPI callback listener publicly.

The IPI callback listener binds to localhost by default. When testing
cloud or SaaS AI platforms, the target cannot reach ``127.0.0.1``.
This module provides an adapter abstraction for launching a
reverse-tunnel provider alongside the listener so the callback URL is
publicly reachable over the internet.

Currently only Cloudflare Quick Tunnels are implemented, but the
:class:`TunnelAdapter` abstract base class defines the contract any
additional provider (ngrok, SSH, etc.) must satisfy.

Example:
    >>> from q_ai.ipi.tunnel import get_tunnel_adapter
    >>> adapter = get_tunnel_adapter("cloudflare")
    >>> if adapter.is_available():
    ...     public_url = adapter.start(local_port=8080)
    ...     try:
    ...         print(f"Listener publicly reachable at {public_url}")
    ...     finally:
    ...         adapter.stop()
"""

from __future__ import annotations

import contextlib
import queue
import re
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod

DEFAULT_STARTUP_TIMEOUT_SECS = 30.0
"""Default seconds to wait for a tunnel to announce its public URL."""

_STOP_GRACE_PERIOD_SECS = 3.0
"""Seconds to wait for ``terminate()`` before escalating to ``kill()``."""

_CLOUDFLARE_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
"""Regex matching a Cloudflare Quick Tunnel URL in cloudflared stderr."""

_CLOUDFLARED_BINARY = "cloudflared"
"""Name of the cloudflared executable on PATH."""

_CLOUDFLARED_INSTALL_INSTRUCTIONS = """\
cloudflared is not installed or not on PATH.

Install cloudflared:
  macOS:   brew install cloudflared
  Linux:   sudo apt install cloudflared       (Debian / Ubuntu)
           sudo snap install cloudflared      (Snap)
  Windows: winget install --id Cloudflare.cloudflared
           scoop install cloudflared

Or download a prebuilt binary directly:
  https://github.com/cloudflare/cloudflared/releases/latest
"""
"""Install guidance surfaced when ``cloudflared`` is missing."""


class TunnelError(Exception):
    """Base exception for tunnel adapter errors."""


class TunnelBinaryNotFoundError(TunnelError):
    """Raised when the tunnel provider's binary is not available on PATH."""


class TunnelStartupError(TunnelError):
    """Raised when the tunnel subprocess fails to announce a public URL."""


class TunnelAdapter(ABC):
    """Abstract contract for a reverse-tunnel provider.

    Implementations wrap a provider-specific CLI (``cloudflared``,
    ``ngrok``, etc.) and expose a uniform start/stop lifecycle plus URL
    discovery. Adapters are single-use — a new instance must be created
    per tunnel.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. ``"cloudflare"``)."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether the provider binary is available on PATH."""

    @abstractmethod
    def install_instructions(self) -> str:
        """Return platform-neutral install guidance for the provider."""

    @abstractmethod
    def start(
        self,
        local_port: int,
        timeout: float = DEFAULT_STARTUP_TIMEOUT_SECS,
    ) -> str:
        """Start the tunnel and return the public URL.

        Args:
            local_port: TCP port the tunnel should forward to on
                localhost.
            timeout: Seconds to wait for the provider to announce a
                public URL before giving up.

        Returns:
            The public HTTPS URL that routes to ``localhost:local_port``.

        Raises:
            TunnelBinaryNotFoundError: If the provider binary is not
                available on PATH.
            TunnelStartupError: If the provider fails to announce a
                public URL within ``timeout`` seconds.
            TunnelError: For any other start failure.
        """

    @abstractmethod
    def stop(self) -> None:
        """Terminate the tunnel subprocess and clean up resources.

        Idempotent: calling ``stop()`` on an adapter that has not been
        started, or has already been stopped, is a no-op.
        """


class CloudflareTunnelAdapter(TunnelAdapter):
    """Cloudflare Quick Tunnel adapter using the ``cloudflared`` CLI.

    Spawns ``cloudflared tunnel --url http://localhost:{port}`` and
    parses the announced ``trycloudflare.com`` URL from the subprocess's
    stderr. A background daemon thread reads stderr into a
    :class:`queue.Queue` so the main thread can block on URL discovery
    without risk of OS pipe deadlock.
    """

    @property
    def provider_name(self) -> str:
        """Return ``"cloudflare"``."""
        return "cloudflare"

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None

    def is_available(self) -> bool:
        """Return whether ``cloudflared`` is on PATH."""
        return shutil.which(_CLOUDFLARED_BINARY) is not None

    def install_instructions(self) -> str:
        """Return install guidance for ``cloudflared``."""
        return _CLOUDFLARED_INSTALL_INSTRUCTIONS

    def start(
        self,
        local_port: int,
        timeout: float = DEFAULT_STARTUP_TIMEOUT_SECS,
    ) -> str:
        """Start cloudflared and return the announced public URL.

        See :meth:`TunnelAdapter.start` for semantics.
        """
        if self._process is not None:
            raise TunnelError("Tunnel already started; construct a new adapter")

        if not self.is_available():
            raise TunnelBinaryNotFoundError(self.install_instructions())

        cmd = [
            _CLOUDFLARED_BINARY,
            "tunnel",
            "--url",
            f"http://localhost:{local_port}",
        ]
        # cloudflared prints the tunnel URL to stderr, not stdout.
        self._process = subprocess.Popen(  # noqa: S603 - cmd is hardcoded
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        url_queue: queue.Queue[str] = queue.Queue()
        self._stderr_thread = threading.Thread(
            target=self._read_stderr_for_url,
            args=(self._process, url_queue),
            daemon=True,
        )
        self._stderr_thread.start()

        try:
            announced = url_queue.get(timeout=timeout)
        except queue.Empty as exc:
            self.stop()
            raise TunnelStartupError(
                f"cloudflared did not announce a public URL within {timeout:.0f}s"
            ) from exc

        if not announced:
            # Sentinel: stderr closed before a URL was seen (process exited).
            exit_code = self._process.poll() if self._process else None
            self.stop()
            raise TunnelStartupError(
                f"cloudflared exited (code={exit_code}) before announcing a public URL"
            )

        return announced

    @staticmethod
    def _read_stderr_for_url(
        process: subprocess.Popen[bytes],
        url_queue: queue.Queue[str],
    ) -> None:
        """Background worker: read stderr, enqueue the first tunnel URL.

        On stderr EOF (subprocess exited), enqueue an empty-string
        sentinel so the caller unblocks immediately instead of waiting
        for the full timeout.
        """
        if process.stderr is None:
            url_queue.put("")
            return
        try:
            for raw_line in iter(process.stderr.readline, b""):
                line = raw_line.decode("utf-8", errors="replace")
                match = _CLOUDFLARE_URL_PATTERN.search(line)
                if match:
                    url_queue.put(match.group(0))
                    return
        except (OSError, ValueError):
            # Pipe closed or decode failure — fall through to sentinel.
            pass
        url_queue.put("")

    def stop(self) -> None:
        """Terminate cloudflared and release the subprocess handle."""
        if self._process is None:
            return
        try:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=_STOP_GRACE_PERIOD_SECS)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
        finally:
            if self._process.stderr is not None:
                with contextlib.suppress(OSError):
                    self._process.stderr.close()
            self._process = None
            self._stderr_thread = None


def get_tunnel_adapter(provider: str) -> TunnelAdapter:
    """Return a tunnel adapter for the given provider name.

    Args:
        provider: Provider identifier (currently only ``"cloudflare"``).

    Returns:
        A fresh :class:`TunnelAdapter` instance.

    Raises:
        ValueError: If ``provider`` is not a recognized provider name.
    """
    if provider == "cloudflare":
        return CloudflareTunnelAdapter()
    raise ValueError(f"Unknown tunnel provider: {provider!r}")


__all__ = [
    "DEFAULT_STARTUP_TIMEOUT_SECS",
    "CloudflareTunnelAdapter",
    "TunnelAdapter",
    "TunnelBinaryNotFoundError",
    "TunnelError",
    "TunnelStartupError",
    "get_tunnel_adapter",
]
