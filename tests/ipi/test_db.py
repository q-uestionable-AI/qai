"""CRUD tests for q_ai.ipi.db — ipi_payloads and ipi_hits tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from q_ai.ipi.db import (
    get_all_campaigns,
    get_campaign,
    get_campaign_by_token,
    get_hits,
    reset_db,
    save_campaign,
    save_hit,
)
from q_ai.ipi.models import Campaign, Hit, HitConfidence

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_campaign(
    *,
    campaign_id: str | None = None,
    uuid_val: str | None = None,
    token: str = "tok_abc123",
    filename: str = "payload.pdf",
    doc_format: str = "pdf",
    technique: str = "white_ink",
    callback_url: str = "http://example.com/cb",
    output_path: str | None = None,
    payload_style: str = "obvious",
    payload_type: str = "callback",
    run_id: str | None = None,
    template_id: str | None = None,
    created_at: datetime | None = None,
) -> Campaign:
    """Create a Campaign instance with sensible defaults."""
    return Campaign(
        id=campaign_id or uuid.uuid4().hex,
        uuid=uuid_val or uuid.uuid4().hex,
        token=token,
        filename=filename,
        format=doc_format,
        technique=technique,
        callback_url=callback_url,
        output_path=output_path,
        payload_style=payload_style,
        payload_type=payload_type,
        run_id=run_id,
        template_id=template_id,
        created_at=created_at or datetime.now(UTC),
    )


def _make_hit(
    *,
    hit_id: str | None = None,
    uuid_val: str = "test-uuid-1234",
    source_ip: str = "127.0.0.1",
    user_agent: str = "python-requests/2.31",
    headers: str = '{"content-type": "application/json"}',
    body: str | None = None,
    token_valid: bool = False,
    via_tunnel: bool = False,
    confidence: HitConfidence = HitConfidence.MEDIUM,
    timestamp: datetime | None = None,
) -> Hit:
    """Create a Hit instance with sensible defaults."""
    return Hit(
        id=hit_id or uuid.uuid4().hex,
        uuid=uuid_val,
        source_ip=source_ip,
        user_agent=user_agent,
        headers=headers,
        body=body,
        token_valid=token_valid,
        via_tunnel=via_tunnel,
        confidence=confidence,
        timestamp=timestamp or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# save_campaign + get_campaign
# ---------------------------------------------------------------------------


class TestSaveCampaignGetCampaign:
    """Round-trip: save a campaign and retrieve it by UUID."""

    def test_save_and_get_campaign(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign()
        save_campaign(campaign, db_path=db)

        result = get_campaign(campaign.uuid, db_path=db)
        assert result is not None
        assert result.uuid == campaign.uuid
        assert result.token == campaign.token
        assert result.filename == campaign.filename
        assert result.format == campaign.format
        assert result.technique == campaign.technique
        assert result.callback_url == campaign.callback_url
        assert result.payload_style == campaign.payload_style
        assert result.payload_type == campaign.payload_type

    def test_get_campaign_returns_id(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign()
        save_campaign(campaign, db_path=db)

        result = get_campaign(campaign.uuid, db_path=db)
        assert result is not None
        assert result.id == campaign.id
        # run_id is nullable (no FK row created in tests); verify None round-trips
        assert result.run_id is None

    def test_get_campaign_output_path(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        output_file = tmp_path / "payload.pdf"
        output_file.write_bytes(b"dummy")
        campaign = _make_campaign(output_path=str(output_file))
        save_campaign(campaign, db_path=db)

        result = get_campaign(campaign.uuid, db_path=db)
        assert result is not None
        assert result.output_path == str(output_file)

    def test_get_campaign_created_at_round_trips(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        ts = datetime(2025, 6, 15, 10, 30, 0, tzinfo=UTC)
        campaign = _make_campaign(created_at=ts)
        save_campaign(campaign, db_path=db)

        result = get_campaign(campaign.uuid, db_path=db)
        assert result is not None
        assert result.created_at.replace(tzinfo=UTC) == ts

    def test_default_campaign_has_none_template_id(self) -> None:
        """Campaign() without template_id defaults to None for legacy call sites."""
        campaign = _make_campaign()
        assert campaign.template_id is None

    def test_template_id_round_trips(self, tmp_path: Path) -> None:
        """A non-null template_id round-trips through save + get_campaign."""
        db = tmp_path / "test.db"
        campaign = _make_campaign(template_id="whois")
        save_campaign(campaign, db_path=db)

        result = get_campaign(campaign.uuid, db_path=db)
        assert result is not None
        assert result.template_id == "whois"

    def test_null_template_id_round_trips(self, tmp_path: Path) -> None:
        """A NULL template_id round-trips as None (pre-migration legacy path)."""
        db = tmp_path / "test.db"
        campaign = _make_campaign(template_id=None)
        save_campaign(campaign, db_path=db)

        result = get_campaign(campaign.uuid, db_path=db)
        assert result is not None
        assert result.template_id is None


# ---------------------------------------------------------------------------
# Campaign.to_dict / Campaign.from_row round-trip
# ---------------------------------------------------------------------------


class TestCampaignDictRoundTrip:
    """Campaign.to_dict and Campaign.from_row preserve template_id.

    The db-layer persistence helpers (save_campaign + _row_to_campaign)
    carry template_id correctly; these tests pin the dataclass's own
    (de)serialization surface, which is also used by JSON exports and
    any future API response path.
    """

    def test_to_dict_includes_template_id(self) -> None:
        """to_dict's payload contains the field with the given value."""
        campaign = _make_campaign(template_id="whois")
        payload = campaign.to_dict()
        assert "template_id" in payload
        assert payload["template_id"] == "whois"

    def test_to_dict_preserves_none_template_id(self) -> None:
        """to_dict emits the key even when the value is None."""
        campaign = _make_campaign(template_id=None)
        payload = campaign.to_dict()
        assert "template_id" in payload
        assert payload["template_id"] is None

    def test_from_row_restores_template_id(self) -> None:
        """from_row reconstructs a Campaign with the row's template_id."""
        row = {
            "id": "rt-id",
            "uuid": "rt-uuid",
            "token": "rt-token",
            "filename": "rt.pdf",
            "format": "pdf",
            "technique": "white_ink",
            "callback_url": "http://example.com/cb",
            "output_path": "/tmp/rt.pdf",
            "payload_style": "obvious",
            "payload_type": "callback",
            "run_id": None,
            "template_id": "whois",
            "created_at": "2026-04-17T12:00:00+00:00",
        }
        campaign = Campaign.from_row(row)
        assert campaign.template_id == "whois"

    def test_from_row_defaults_template_id_to_none_when_missing(self) -> None:
        """from_row tolerates rows without a template_id key (pre-v13)."""
        row = {
            "id": "rt-id",
            "uuid": "rt-uuid",
            "token": "rt-token",
            "filename": "rt.pdf",
            "format": "pdf",
            "technique": "white_ink",
            "callback_url": "http://example.com/cb",
            "created_at": "2026-04-17T12:00:00+00:00",
        }
        campaign = Campaign.from_row(row)
        assert campaign.template_id is None

    def test_to_dict_from_row_round_trip(self) -> None:
        """to_dict → from_row preserves template_id across the boundary."""
        original = _make_campaign(template_id="whois")
        rehydrated = Campaign.from_row(original.to_dict())
        assert rehydrated.template_id == "whois"


# ---------------------------------------------------------------------------
# get_campaign — nonexistent UUID
# ---------------------------------------------------------------------------


class TestGetCampaignNotFound:
    """get_campaign returns None for unknown UUIDs."""

    def test_nonexistent_uuid_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        result = get_campaign("does-not-exist", db_path=db)
        assert result is None

    def test_nonexistent_uuid_on_populated_db_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_campaign(_make_campaign(), db_path=db)
        result = get_campaign("does-not-exist", db_path=db)
        assert result is None


# ---------------------------------------------------------------------------
# get_campaign_by_token
# ---------------------------------------------------------------------------


class TestGetCampaignByToken:
    """Token-based lookup: both uuid and token must match."""

    def test_valid_token_returns_campaign(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign(token="secret99")
        save_campaign(campaign, db_path=db)

        result = get_campaign_by_token(campaign.uuid, "secret99", db_path=db)
        assert result is not None
        assert result.uuid == campaign.uuid

    def test_wrong_token_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign(token="correct_token")
        save_campaign(campaign, db_path=db)

        result = get_campaign_by_token(campaign.uuid, "wrong_token", db_path=db)
        assert result is None

    def test_wrong_uuid_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign(token="mytoken")
        save_campaign(campaign, db_path=db)

        result = get_campaign_by_token("bad-uuid", "mytoken", db_path=db)
        assert result is None

    def test_empty_db_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        result = get_campaign_by_token("any-uuid", "any-token", db_path=db)
        assert result is None


# ---------------------------------------------------------------------------
# get_all_campaigns
# ---------------------------------------------------------------------------


class TestGetAllCampaigns:
    """get_all_campaigns returns all campaigns ordered newest-first."""

    def test_empty_db_returns_empty_list(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        result = get_all_campaigns(db_path=db)
        assert result == []

    def test_single_campaign(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaign = _make_campaign()
        save_campaign(campaign, db_path=db)

        result = get_all_campaigns(db_path=db)
        assert len(result) == 1
        assert result[0].uuid == campaign.uuid

    def test_multiple_campaigns_ordered_newest_first(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        older = _make_campaign(created_at=datetime(2025, 1, 1, tzinfo=UTC))
        newer = _make_campaign(created_at=datetime(2025, 6, 1, tzinfo=UTC))
        # Insert older first, then newer — result should be newest-first
        save_campaign(older, db_path=db)
        save_campaign(newer, db_path=db)

        result = get_all_campaigns(db_path=db)
        assert len(result) == 2
        assert result[0].uuid == newer.uuid
        assert result[1].uuid == older.uuid


# ---------------------------------------------------------------------------
# save_hit + get_hits
# ---------------------------------------------------------------------------


class TestSaveHitGetHits:
    """Round-trip: save hits and retrieve with/without UUID filter."""

    def test_save_and_get_hit_by_uuid(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        hit = _make_hit(uuid_val="camp-uuid-1")
        save_hit(hit, db_path=db)

        results = get_hits(uuid="camp-uuid-1", db_path=db)
        assert len(results) == 1
        assert results[0].uuid == "camp-uuid-1"
        assert results[0].source_ip == hit.source_ip
        assert results[0].user_agent == hit.user_agent
        assert results[0].headers == hit.headers
        assert results[0].confidence == hit.confidence
        assert results[0].token_valid == hit.token_valid

    def test_get_hits_id_is_text(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        hit = _make_hit()
        save_hit(hit, db_path=db)

        results = get_hits(db_path=db)
        assert isinstance(results[0].id, str)

    def test_get_hits_returns_all_when_no_uuid(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_hit(_make_hit(uuid_val="uuid-a"), db_path=db)
        save_hit(_make_hit(uuid_val="uuid-b"), db_path=db)
        save_hit(_make_hit(uuid_val="uuid-a"), db_path=db)

        # No uuid filter — should return all 3 hits
        results = get_hits(db_path=db)
        assert len(results) == 3

    def test_get_hits_filters_by_uuid(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_hit(_make_hit(uuid_val="uuid-a"), db_path=db)
        save_hit(_make_hit(uuid_val="uuid-b"), db_path=db)
        save_hit(_make_hit(uuid_val="uuid-a"), db_path=db)

        a_hits = get_hits(uuid="uuid-a", db_path=db)
        b_hits = get_hits(uuid="uuid-b", db_path=db)
        assert len(a_hits) == 2
        assert len(b_hits) == 1
        assert all(h.uuid == "uuid-a" for h in a_hits)

    def test_get_hits_empty_for_unknown_uuid(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_hit(_make_hit(uuid_val="known-uuid"), db_path=db)

        results = get_hits(uuid="unknown-uuid", db_path=db)
        assert results == []

    def test_hit_body_persisted(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        hit = _make_hit(body="exfil data here")
        save_hit(hit, db_path=db)

        results = get_hits(db_path=db)
        assert results[0].body == "exfil data here"

    def test_hit_token_valid_true(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        hit = _make_hit(token_valid=True, confidence=HitConfidence.HIGH)
        save_hit(hit, db_path=db)

        results = get_hits(db_path=db)
        assert results[0].token_valid is True
        assert results[0].confidence == HitConfidence.HIGH

    def test_get_all_hits_no_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        for i in range(3):
            save_hit(_make_hit(uuid_val=f"uuid-{i}"), db_path=db)

        results = get_hits(db_path=db)
        assert len(results) == 3

    def test_hit_via_tunnel_defaults_false(self) -> None:
        """Hit() without via_tunnel defaults to False — legacy call sites
        that don't know about the field stay untouched."""
        hit = _make_hit()
        assert hit.via_tunnel is False

    def test_hit_via_tunnel_true_round_trips(self, tmp_path: Path) -> None:
        """A tunnel-flagged hit round-trips through save_hit + get_hits."""
        db = tmp_path / "test.db"
        hit = _make_hit(via_tunnel=True, confidence=HitConfidence.HIGH)
        save_hit(hit, db_path=db)

        results = get_hits(db_path=db)
        assert len(results) == 1
        assert results[0].via_tunnel is True

    def test_hit_via_tunnel_false_round_trips(self, tmp_path: Path) -> None:
        """A direct (non-tunnel) hit persists with via_tunnel=False."""
        db = tmp_path / "test.db"
        hit = _make_hit(via_tunnel=False)
        save_hit(hit, db_path=db)

        results = get_hits(db_path=db)
        assert len(results) == 1
        assert results[0].via_tunnel is False

    def test_hit_to_dict_includes_via_tunnel(self) -> None:
        """Hit.to_dict surfaces via_tunnel — CodeRabbit's PR #118 Campaign
        lesson applied to Hit up front."""
        hit = _make_hit(via_tunnel=True)
        payload = hit.to_dict()
        assert "via_tunnel" in payload
        assert payload["via_tunnel"] is True

    def test_hit_to_dict_preserves_false_via_tunnel(self) -> None:
        """to_dict emits the key even when the value is False."""
        hit = _make_hit(via_tunnel=False)
        payload = hit.to_dict()
        assert "via_tunnel" in payload
        assert payload["via_tunnel"] is False

    def test_hit_from_row_restores_via_tunnel(self) -> None:
        """Hit.from_row coerces the INTEGER column to bool."""
        row = {
            "id": "h1",
            "uuid": "u1",
            "source_ip": "1.2.3.4",
            "user_agent": "ua",
            "headers": "{}",
            "confidence": HitConfidence.HIGH.value,
            "timestamp": "2026-04-17T12:00:00+00:00",
            "body": None,
            "token_valid": 1,
            "via_tunnel": 1,
        }
        hit = Hit.from_row(row)
        assert hit.via_tunnel is True

    def test_hit_from_row_defaults_via_tunnel_to_false_when_missing(self) -> None:
        """from_row tolerates rows without via_tunnel (pre-v14 schema)."""
        row = {
            "id": "h1",
            "uuid": "u1",
            "source_ip": "1.2.3.4",
            "user_agent": "ua",
            "headers": "{}",
            "confidence": HitConfidence.LOW.value,
            "timestamp": "2026-04-17T12:00:00+00:00",
        }
        hit = Hit.from_row(row)
        assert hit.via_tunnel is False


# ---------------------------------------------------------------------------
# reset_db
# ---------------------------------------------------------------------------


class TestResetDb:
    """reset_db clears tables and deletes generated files."""

    def test_reset_empty_db_returns_zero_counts(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        campaigns_del, hits_del, files_del = reset_db(db_path=db)
        assert campaigns_del == 0
        assert hits_del == 0
        assert files_del == 0

    def test_reset_clears_campaigns(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_campaign(_make_campaign(), db_path=db)
        save_campaign(_make_campaign(), db_path=db)

        campaigns_del, hits_del, _files_del = reset_db(db_path=db)
        assert campaigns_del == 2
        assert hits_del == 0
        assert get_all_campaigns(db_path=db) == []

    def test_reset_clears_hits(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_hit(_make_hit(), db_path=db)
        save_hit(_make_hit(), db_path=db)
        save_hit(_make_hit(), db_path=db)

        _campaigns_del, hits_del, _files_del = reset_db(db_path=db)
        assert hits_del == 3
        assert get_hits(db_path=db) == []

    def test_reset_deletes_output_files(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        f1 = tmp_path / "payload1.pdf"
        f2 = tmp_path / "payload2.pdf"
        f1.write_bytes(b"pdf1")
        f2.write_bytes(b"pdf2")

        save_campaign(_make_campaign(output_path=str(f1)), db_path=db)
        save_campaign(_make_campaign(output_path=str(f2)), db_path=db)

        _campaigns_del, _hits_del, files_del = reset_db(db_path=db)
        assert files_del == 2
        assert not f1.exists()
        assert not f2.exists()

    def test_reset_skips_missing_files(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        missing = tmp_path / "gone.pdf"
        save_campaign(_make_campaign(output_path=str(missing)), db_path=db)

        campaigns_del, _hits_del, files_del = reset_db(db_path=db)
        assert campaigns_del == 1
        assert files_del == 0  # file was never created

    def test_reset_skips_null_output_paths(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_campaign(_make_campaign(output_path=None), db_path=db)

        campaigns_del, _hits_del, files_del = reset_db(db_path=db)
        assert campaigns_del == 1
        assert files_del == 0

    def test_reset_preserves_schema(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        save_campaign(_make_campaign(), db_path=db)
        reset_db(db_path=db)

        # Should be able to save again after reset (schema intact)
        new_campaign = _make_campaign()
        save_campaign(new_campaign, db_path=db)
        result = get_campaign(new_campaign.uuid, db_path=db)
        assert result is not None
