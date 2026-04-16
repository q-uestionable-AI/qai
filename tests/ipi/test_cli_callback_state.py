"""Tests for CLI integration with the active-callback state file.

Covers:
  - ``qai ipi generate`` auto-discovers the callback URL from a valid
    state file when ``--callback`` is omitted.
  - Explicit ``--callback`` always wins over state.
  - Stale state prints a warning and falls through.
  - ``qai ipi listen --tunnel`` writes the state file on tunnel start
    and deletes it on shutdown.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.ipi.callback_state import CallbackState, state_path
from q_ai.ipi.generate_service import GenerateResult

runner = CliRunner()


def _fake_state(
    *,
    public_url: str = "https://active-tunnel.trycloudflare.com",
    listener_pid: int = 12345,
) -> CallbackState:
    return CallbackState(
        public_url=public_url,
        provider="cloudflare",
        local_host="127.0.0.1",
        local_port=8080,
        listener_pid=listener_pid,
        created_at="2026-04-16T12:00:00+00:00",
        instance_id="testinstance",
    )


# ---------------------------------------------------------------------------
# generate auto-discovery
# ---------------------------------------------------------------------------


class TestGenerateAutoDiscovery:
    """generate reads active-callback state when --callback is omitted."""

    @patch("q_ai.ipi.cli.generate_documents")
    @patch("q_ai.ipi.cli.persist_generate", create=True)
    def test_uses_state_when_callback_omitted(
        self,
        _mock_persist: object,
        mock_gen: object,
    ) -> None:
        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        state = _fake_state()

        with patch(
            "q_ai.ipi.cli.read_valid_state",
            return_value=(state, None),
        ):
            result = runner.invoke(
                app,
                [
                    "ipi",
                    "generate",
                    "--technique",
                    "white_ink",
                    "--format",
                    "pdf",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Using active callback" in result.output
        assert state.public_url in result.output
        kwargs = mock_gen.call_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["callback_url"] == state.public_url

    @patch("q_ai.ipi.cli.generate_documents")
    @patch("q_ai.ipi.cli.persist_generate", create=True)
    def test_explicit_callback_flag_overrides_state(
        self,
        _mock_persist: object,
        mock_gen: object,
    ) -> None:
        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        state = _fake_state(public_url="https://ignored.trycloudflare.com")

        with patch(
            "q_ai.ipi.cli.read_valid_state",
            return_value=(state, None),
        ):
            result = runner.invoke(
                app,
                [
                    "ipi",
                    "generate",
                    "--callback",
                    "http://explicit:9000",
                    "--technique",
                    "white_ink",
                    "--format",
                    "pdf",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Using active callback" not in result.output
        kwargs = mock_gen.call_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["callback_url"] == "http://explicit:9000"

    @patch("q_ai.ipi.cli.generate_documents")
    @patch("q_ai.ipi.cli.persist_generate", create=True)
    def test_positional_callback_overrides_state(
        self,
        _mock_persist: object,
        mock_gen: object,
    ) -> None:
        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        state = _fake_state(public_url="https://ignored.trycloudflare.com")

        with patch(
            "q_ai.ipi.cli.read_valid_state",
            return_value=(state, None),
        ):
            result = runner.invoke(
                app,
                [
                    "ipi",
                    "generate",
                    "http://positional:7000",
                    "--technique",
                    "white_ink",
                    "--format",
                    "pdf",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = mock_gen.call_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["callback_url"] == "http://positional:7000"

    @patch("q_ai.ipi.cli.generate_documents")
    @patch("q_ai.ipi.cli.persist_generate", create=True)
    def test_stale_state_prints_warning_and_falls_through(
        self,
        _mock_persist: object,
        mock_gen: object,
    ) -> None:
        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        stale_warning = "Active-callback state references dead PID 999999; ignoring."

        with patch(
            "q_ai.ipi.cli.read_valid_state",
            return_value=(None, stale_warning),
        ):
            # Provide callback positionally so the prompt doesn't block in non-TTY.
            result = runner.invoke(
                app,
                [
                    "ipi",
                    "generate",
                    "http://fallback:8080",
                    "--technique",
                    "white_ink",
                    "--format",
                    "pdf",
                ],
            )

        assert result.exit_code == 0, result.output
        # The warning is only displayed when the state file is consulted —
        # i.e., when no positional/--callback is given.

    @patch("q_ai.ipi.cli.generate_documents")
    @patch("q_ai.ipi.cli.persist_generate", create=True)
    def test_stale_state_displayed_when_no_explicit_callback(
        self,
        _mock_persist: object,
        mock_gen: object,
    ) -> None:
        """When no callback is given, stale warning is surfaced to the user."""
        mock_gen.return_value = GenerateResult(campaigns=[], errors=[])  # type: ignore[attr-defined]
        stale_warning = "Active-callback state references dead PID 999999; ignoring."

        with patch(
            "q_ai.ipi.cli.read_valid_state",
            return_value=(None, stale_warning),
        ):
            # No callback, no TTY → prompt_or_fail exits. We expect exit code 1
            # but the warning must appear in output beforehand.
            result = runner.invoke(
                app,
                [
                    "ipi",
                    "generate",
                    "--technique",
                    "white_ink",
                    "--format",
                    "pdf",
                ],
            )

        assert "dead PID" in result.output
        # Without a callback in non-TTY, prompt_or_fail exits 1.
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# listen --tunnel writes state + deletes on shutdown
# ---------------------------------------------------------------------------


class TestListenTunnelStateFile:
    """listen --tunnel writes active-callback state on start, removes on exit."""

    def test_state_written_on_tunnel_start_and_removed_on_exit(self) -> None:
        fake_adapter = MagicMock()
        fake_adapter.is_available.return_value = True
        fake_adapter.start.return_value = "https://state-test.trycloudflare.com"

        writes: list[object] = []
        deletes: list[object] = []

        def _capture_write(state: object, qai_dir: object = None) -> object:
            writes.append(state)
            # Return a sentinel path for signature fidelity.
            return state_path(None)

        def _capture_delete(qai_dir: object = None) -> None:
            deletes.append(object())

        with (
            patch("q_ai.ipi.cli.get_tunnel_adapter", return_value=fake_adapter),
            patch("q_ai.ipi.cli.start_server") as mock_start,
            patch("q_ai.ipi.cli.write_state", side_effect=_capture_write) as mock_write,
            patch("q_ai.ipi.cli.delete_state", side_effect=_capture_delete),
        ):
            result = runner.invoke(app, ["ipi", "listen", "--tunnel", "cloudflare"])

        assert result.exit_code == 0, result.output
        mock_start.assert_called_once()
        mock_write.assert_called_once()
        # The written state must carry the tunnel's public URL + metadata.
        written_state = mock_write.call_args.args[0]
        assert written_state.public_url == "https://state-test.trycloudflare.com"
        assert written_state.provider == "cloudflare"
        assert written_state.local_port == 8080
        # delete_state called in the finally block after start_server returns.
        assert len(deletes) == 1

    def test_state_deleted_even_when_start_server_raises(self) -> None:
        fake_adapter = MagicMock()
        fake_adapter.is_available.return_value = True
        fake_adapter.start.return_value = "https://crash-test.trycloudflare.com"

        deletes: list[object] = []

        def _capture_delete(qai_dir: object = None) -> None:
            deletes.append(object())

        class _SomeError(Exception):
            pass

        with (
            patch("q_ai.ipi.cli.get_tunnel_adapter", return_value=fake_adapter),
            patch("q_ai.ipi.cli.start_server", side_effect=_SomeError("boom")),
            patch("q_ai.ipi.cli.write_state"),
            patch("q_ai.ipi.cli.delete_state", side_effect=_capture_delete),
        ):
            result = runner.invoke(app, ["ipi", "listen", "--tunnel", "cloudflare"])

        # The exception propagates out of typer; result.exception reflects it.
        assert isinstance(result.exception, _SomeError)
        # Cleanup ran regardless.
        assert len(deletes) == 1
        fake_adapter.stop.assert_called_once()
