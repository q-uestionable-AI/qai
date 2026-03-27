"""Tests for q_ai.proxy.mapper -- persist_session."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from mcp.types import JSONRPCMessage, JSONRPCRequest

from q_ai.core.db import create_run, get_connection
from q_ai.mcp.models import Direction, Transport
from q_ai.proxy.mapper import persist_session
from q_ai.proxy.models import ProxyMessage
from q_ai.proxy.session_store import SessionStore


def _make_store(
    num_messages: int = 3,
    transport: Transport = Transport.STDIO,
    server_command: str = "python server.py",
) -> SessionStore:
    """Build a SessionStore with some messages."""
    store = SessionStore(
        session_id=str(uuid.uuid4()),
        transport=transport,
        server_command=server_command,
        started_at=datetime.now(tz=UTC),
    )
    for i in range(num_messages):
        raw = JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=i + 1, method="tools/list"))
        msg = ProxyMessage(
            id=str(uuid.uuid4()),
            sequence=i,
            timestamp=datetime.now(tz=UTC),
            direction=Direction.CLIENT_TO_SERVER,
            transport=transport,
            raw=raw,
            jsonrpc_id=i + 1,
            method="tools/list",
            correlated_id=None,
            modified=False,
            original_raw=None,
        )
        store.append(msg)
    return store


class TestPersistSession:
    """persist_session() writes run + proxy_sessions + artifacts."""

    def test_creates_run_and_session(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = _make_store(num_messages=5)

        run_id = persist_session(store, db_path=db_path, artifacts_dir=tmp_path / "artifacts")

        assert run_id  # non-empty string

        with get_connection(db_path) as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert run is not None
            assert run["module"] == "proxy"

            session = conn.execute(
                "SELECT * FROM proxy_sessions WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert session is not None
            assert session["transport"] == "stdio"
            assert session["server_name"] == "python server.py"
            assert session["message_count"] == 5

    def test_saves_session_json(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        artifacts_dir = tmp_path / "artifacts"
        store = _make_store(num_messages=2)

        run_id = persist_session(store, db_path=db_path, artifacts_dir=artifacts_dir)

        with get_connection(db_path) as conn:
            session = conn.execute(
                "SELECT session_file FROM proxy_sessions WHERE run_id = ?", (run_id,)
            ).fetchone()
            session_file = session["session_file"]

        # Session file should exist under tmp artifacts dir
        full_path = artifacts_dir / session_file
        assert full_path.exists()

    def test_run_status_completed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = _make_store()

        run_id = persist_session(store, db_path=db_path, artifacts_dir=tmp_path / "artifacts")

        with get_connection(db_path) as conn:
            run = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert run["status"] == 2  # COMPLETED

    def test_sse_transport_persisted(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = SessionStore(
            session_id=str(uuid.uuid4()),
            transport=Transport.SSE,
            server_url="http://localhost:3000/sse",
            started_at=datetime.now(tz=UTC),
        )

        run_id = persist_session(store, db_path=db_path, artifacts_dir=tmp_path / "artifacts")

        with get_connection(db_path) as conn:
            session = conn.execute(
                "SELECT * FROM proxy_sessions WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert session["transport"] == "sse"
            assert session["server_name"] == "http://localhost:3000/sse"

    def test_persist_with_explicit_run_id(self, tmp_path: Path) -> None:
        """Verify persist_session uses provided run_id instead of creating a new one."""
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            pre_run_id = create_run(conn, module="proxy", name="pre-created")

        store = _make_store(num_messages=2)

        run_id = persist_session(
            store,
            db_path=db_path,
            artifacts_dir=tmp_path / "artifacts",
            run_id=pre_run_id,
        )
        assert run_id == pre_run_id

        with get_connection(db_path) as conn:
            # Should only have the pre-created run
            runs = conn.execute("SELECT * FROM runs").fetchall()
            assert len(runs) == 1
            assert runs[0]["id"] == pre_run_id

            # Session should reference the pre-created run
            session = conn.execute(
                "SELECT * FROM proxy_sessions WHERE run_id = ?", (pre_run_id,)
            ).fetchone()
            assert session is not None
            assert session["message_count"] == 2

    def test_chain_correlation_fields(self, tmp_path: Path) -> None:
        """Verify chain_run_id and chain_step_id are persisted."""
        db_path = tmp_path / "test.db"
        store = SessionStore(
            session_id=str(uuid.uuid4()),
            transport=Transport.STDIO,
            server_command="python server.py",
            started_at=datetime.now(tz=UTC),
            chain_run_id="chain-run-123",
            chain_step_id="step-audit",
        )

        run_id = persist_session(store, db_path=db_path, artifacts_dir=tmp_path / "artifacts")

        with get_connection(db_path) as conn:
            session = conn.execute(
                "SELECT chain_run_id, chain_step_id FROM proxy_sessions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            assert session["chain_run_id"] == "chain-run-123"
            assert session["chain_step_id"] == "step-audit"

    def test_chain_correlation_fields_null(self, tmp_path: Path) -> None:
        """chain_run_id and chain_step_id default to NULL."""
        db_path = tmp_path / "test.db"
        store = _make_store(num_messages=1)

        run_id = persist_session(store, db_path=db_path, artifacts_dir=tmp_path / "artifacts")

        with get_connection(db_path) as conn:
            session = conn.execute(
                "SELECT chain_run_id, chain_step_id FROM proxy_sessions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            assert session["chain_run_id"] is None
            assert session["chain_step_id"] is None
