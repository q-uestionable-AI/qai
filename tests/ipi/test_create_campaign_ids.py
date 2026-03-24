"""Tests for create_campaign_ids — token generation without ambiguous characters."""

from __future__ import annotations

from q_ai.ipi.generators import create_campaign_ids

_AMBIGUOUS_CHARS = frozenset("0O1lI")


class TestCreateCampaignIds:
    """create_campaign_ids produces unambiguous, reproducible tokens."""

    def test_random_token_has_no_ambiguous_chars(self) -> None:
        for _ in range(50):
            _uuid, token = create_campaign_ids()
            assert not _AMBIGUOUS_CHARS.intersection(token), (
                f"Token contains ambiguous chars: {token}"
            )

    def test_random_token_length(self) -> None:
        _uuid, token = create_campaign_ids()
        assert len(token) == 22

    def test_random_uuid_is_valid(self) -> None:
        uid, _token = create_campaign_ids()
        # UUID v4 format: 8-4-4-4-12 hex digits
        assert len(uid) == 36
        assert uid.count("-") == 4

    def test_deterministic_token_has_no_ambiguous_chars(self) -> None:
        for seq in range(20):
            _uuid, token = create_campaign_ids(seed=42, sequence=seq)
            assert not _AMBIGUOUS_CHARS.intersection(token), (
                f"Deterministic token contains ambiguous chars: {token}"
            )

    def test_deterministic_token_length(self) -> None:
        _uuid, token = create_campaign_ids(seed=99, sequence=0)
        assert len(token) == 22

    def test_deterministic_reproducibility(self) -> None:
        uid1, tok1 = create_campaign_ids(seed=42, sequence=0)
        uid2, tok2 = create_campaign_ids(seed=42, sequence=0)
        assert uid1 == uid2
        assert tok1 == tok2

    def test_deterministic_different_sequence_differs(self) -> None:
        uid1, tok1 = create_campaign_ids(seed=42, sequence=0)
        uid2, tok2 = create_campaign_ids(seed=42, sequence=1)
        assert uid1 != uid2
        assert tok1 != tok2
