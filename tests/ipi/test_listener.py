"""Tests for q_ai.ipi.listener — confidence scoring and hit recording."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from q_ai.core.db import get_connection, list_findings
from q_ai.core.models import Severity
from q_ai.ipi.db import save_campaign
from q_ai.ipi.listener import record_hit, score_confidence
from q_ai.ipi.models import Campaign, Hit, HitConfidence

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_campaign(
    *,
    campaign_id: str | None = None,
    uuid_val: str | None = None,
    token: str = "tok_abc123",
    run_id: str | None = None,
) -> Campaign:
    """Create a Campaign instance with sensible defaults."""
    return Campaign(
        id=campaign_id or uuid.uuid4().hex,
        uuid=uuid_val or uuid.uuid4().hex,
        token=token,
        filename="payload.pdf",
        format="pdf",
        technique="white_ink",
        callback_url="http://example.com/cb",
        run_id=run_id,
        created_at=datetime.now(UTC),
    )


def _make_hit(
    *,
    hit_id: str | None = None,
    uuid_val: str = "test-uuid-1234",
    user_agent: str = "python-requests/2.31",
    token_valid: bool = False,
    confidence: HitConfidence = HitConfidence.MEDIUM,
) -> Hit:
    """Create a Hit instance with sensible defaults."""
    return Hit(
        id=hit_id or uuid.uuid4().hex,
        uuid=uuid_val,
        source_ip="127.0.0.1",
        user_agent=user_agent,
        headers='{"content-type": "application/json"}',
        token_valid=token_valid,
        confidence=confidence,
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# score_confidence
# ---------------------------------------------------------------------------


class TestScoreConfidence:
    """score_confidence maps token validity + User-Agent to HitConfidence."""

    def test_valid_token_returns_high(self) -> None:
        assert score_confidence(True, "Mozilla/5.0 Chrome/120") == HitConfidence.HIGH

    def test_no_token_python_requests_ua_returns_medium(self) -> None:
        assert score_confidence(False, "python-requests/2.32") == HitConfidence.MEDIUM

    def test_no_token_browser_ua_returns_low(self) -> None:
        assert score_confidence(False, "Mozilla/5.0 Chrome/120") == HitConfidence.LOW

    def test_no_token_httpx_ua_returns_medium(self) -> None:
        assert score_confidence(False, "python-httpx/0.27") == HitConfidence.MEDIUM

    def test_no_token_curl_ua_returns_medium(self) -> None:
        assert score_confidence(False, "curl/8.0") == HitConfidence.MEDIUM

    def test_no_token_mcp_ua_returns_medium(self) -> None:
        ua = "ModelContextProtocol/1.0 (Autonomous; +https://github.com/modelcontextprotocol/servers)"
        assert score_confidence(False, ua) == HitConfidence.MEDIUM


# ---------------------------------------------------------------------------
# record_hit
# ---------------------------------------------------------------------------


class TestRecordHit:
    """record_hit persists hits and creates findings for HIGH/MEDIUM confidence."""

    def test_high_confidence_with_run_id_creates_critical_finding(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign(run_id="run-1")

        # Insert the run record first (FK constraint on ipi_payloads.run_id)
        with get_connection(db) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, name, status, started_at) VALUES (?, ?, ?, ?, ?)",
                ("run-1", "ipi", "test", 2, "2026-03-14T00:00:00"),
            )

        save_campaign(campaign, db_path=db)

        hit = _make_hit(uuid_val=campaign.uuid, token_valid=True, confidence=HitConfidence.HIGH)
        record_hit(hit, db_path=db)

        with get_connection(db) as conn:
            findings = list_findings(conn, run_id="run-1")

        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_medium_confidence_with_run_id_creates_high_finding(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign(run_id="run-1")

        with get_connection(db) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, name, status, started_at) VALUES (?, ?, ?, ?, ?)",
                ("run-1", "ipi", "test", 2, "2026-03-14T00:00:00"),
            )

        save_campaign(campaign, db_path=db)

        hit = _make_hit(uuid_val=campaign.uuid, confidence=HitConfidence.MEDIUM)
        record_hit(hit, db_path=db)

        with get_connection(db) as conn:
            findings = list_findings(conn, run_id="run-1")

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_low_confidence_creates_no_finding(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign(run_id="run-1")

        with get_connection(db) as conn:
            conn.execute(
                "INSERT INTO runs (id, module, name, status, started_at) VALUES (?, ?, ?, ?, ?)",
                ("run-1", "ipi", "test", 2, "2026-03-14T00:00:00"),
            )

        save_campaign(campaign, db_path=db)

        hit = _make_hit(uuid_val=campaign.uuid, confidence=HitConfidence.LOW)
        record_hit(hit, db_path=db)

        with get_connection(db) as conn:
            findings = list_findings(conn, module="ipi")

        assert len(findings) == 0

    def test_high_confidence_without_run_id_creates_adhoc_run_and_finding(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "test.db"
        # Campaign has no run_id — simulates standalone/legacy campaign
        campaign = _make_campaign(run_id=None)
        save_campaign(campaign, db_path=db)

        hit = _make_hit(uuid_val=campaign.uuid, token_valid=True, confidence=HitConfidence.HIGH)
        record_hit(hit, db_path=db)

        with get_connection(db) as conn:
            findings = list_findings(conn, module="ipi")

        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
