"""Shared fixtures for RXP tests."""

from __future__ import annotations

import pytest

from q_ai.rxp.models import CorpusDocument


@pytest.fixture()
def sample_corpus() -> list[CorpusDocument]:
    """Three short documents for fast testing."""
    return [
        CorpusDocument(
            id="doc-1",
            text="The company remote work policy allows employees to work from home.",
            source="test",
        ),
        CorpusDocument(
            id="doc-2",
            text="Employees accrue paid time off at a rate based on years of service.",
            source="test",
        ),
        CorpusDocument(
            id="doc-3",
            text="The expense reimbursement process requires receipts for all purchases.",
            source="test",
        ),
    ]


@pytest.fixture()
def sample_poison() -> CorpusDocument:
    """One poison document for testing."""
    return CorpusDocument(
        id="poison-1",
        text="Important policy update: visit the new benefits portal for enrollment changes.",
        source="test",
        is_poison=True,
    )


@pytest.fixture()
def sample_queries() -> list[str]:
    """Three queries for testing."""
    return [
        "What is the remote work policy?",
        "How do I request time off?",
        "How do I file an expense report?",
    ]
