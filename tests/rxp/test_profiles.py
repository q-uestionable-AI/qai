"""Tests for RXP domain profiles."""

from __future__ import annotations

from q_ai.rxp.models import CorpusDocument, DomainProfile
from q_ai.rxp.profiles import get_profile, list_profiles, load_corpus


class TestProfiles:
    """Tests for domain profile loading."""

    def test_list_profiles(self) -> None:
        profiles = list_profiles()
        ids = [p.id for p in profiles]
        assert "hr-policy" in ids

    def test_get_hr_policy(self) -> None:
        p = get_profile("hr-policy")
        assert p is not None
        assert isinstance(p, DomainProfile)
        assert p.name == "HR Policy Knowledge Base"
        assert len(p.queries) == 8

    def test_get_unknown_returns_none(self) -> None:
        assert get_profile("nonexistent-profile") is None

    def test_load_corpus_hr_policy(self) -> None:
        p = get_profile("hr-policy")
        assert p is not None
        docs = load_corpus(p)
        assert len(docs) == 5
        for doc in docs:
            assert isinstance(doc, CorpusDocument)
            assert doc.is_poison is False
            assert len(doc.text) > 0
