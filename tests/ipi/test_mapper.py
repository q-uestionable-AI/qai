"""Tests for IPI mapper — persist_generate creates run and links campaigns."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from q_ai.core.db import get_connection
from q_ai.ipi.db import save_campaign
from q_ai.ipi.mapper import persist_generate
from q_ai.ipi.models import Campaign


def _make_campaign() -> Campaign:
    return Campaign(
        id=uuid.uuid4().hex,
        uuid=uuid.uuid4().hex,
        token="test-token",
        filename="test.pdf",
        format="pdf",
        technique="white_ink",
        payload_style="obvious",
        payload_type="callback",
        callback_url="http://localhost:8080",
        created_at=datetime(2026, 3, 14, tzinfo=UTC),
    )


class TestPersistGenerate:
    def test_creates_run_record(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        c = _make_campaign()
        save_campaign(c, db_path=db_path)
        run_id = persist_generate([c], db_path=db_path)
        assert run_id
        with get_connection(db_path) as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert run is not None
            assert run["module"] == "ipi"

    def test_links_campaigns_to_run(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        c1 = _make_campaign()
        c2 = _make_campaign()
        save_campaign(c1, db_path=db_path)
        save_campaign(c2, db_path=db_path)
        run_id = persist_generate([c1, c2], db_path=db_path)
        with get_connection(db_path) as conn:
            rows = conn.execute("SELECT * FROM ipi_payloads WHERE run_id = ?", (run_id,)).fetchall()
            assert len(rows) == 2

    def test_empty_campaigns_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        run_id = persist_generate([], db_path=db_path)
        assert run_id
        with get_connection(db_path) as conn:
            run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            assert run is not None
