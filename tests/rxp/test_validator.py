"""Tests for RXP retrieval validation engine."""

from __future__ import annotations

import pytest

from q_ai.rxp.registry import resolve_model

pytest.importorskip("sentence_transformers")
pytest.importorskip("chromadb")

from q_ai.rxp.models import CorpusDocument, ValidationResult
from q_ai.rxp.validator import validate_retrieval


@pytest.fixture()
def corpus() -> list[CorpusDocument]:
    """Simple corpus for validation testing."""
    return [
        CorpusDocument(id="doc-1", text="Company remote work guidelines and policy", source="t"),
        CorpusDocument(id="doc-2", text="Employee time off and vacation procedures", source="t"),
        CorpusDocument(id="doc-3", text="Expense report submission and reimbursement", source="t"),
    ]


@pytest.fixture()
def poison() -> list[CorpusDocument]:
    """Poison document for validation testing."""
    return [
        CorpusDocument(
            id="poison-1",
            text="Important policy update about remote work and benefits changes",
            source="t",
            is_poison=True,
        ),
    ]


@pytest.mark.integration
class TestValidator:
    """Tests for the retrieval validation engine (requires deps)."""

    def test_validate_retrieval_returns_result(
        self, corpus: list[CorpusDocument], poison: list[CorpusDocument]
    ) -> None:
        result = validate_retrieval(
            corpus_docs=corpus,
            poison_docs=poison,
            queries=["What is the remote work policy?"],
            model_id="minilm-l6",
            top_k=3,
        )
        assert isinstance(result, ValidationResult)
        assert result.model_id == "minilm-l6"
        assert result.total_queries == 1

    def test_validate_retrieval_rate_calculation(
        self, corpus: list[CorpusDocument], poison: list[CorpusDocument]
    ) -> None:
        queries = ["remote work policy", "vacation time off", "expense report"]
        result = validate_retrieval(
            corpus_docs=corpus,
            poison_docs=poison,
            queries=queries,
            model_id="minilm-l6",
            top_k=5,
        )
        assert result.total_queries == 3
        assert 0.0 <= result.retrieval_rate <= 1.0
        assert result.poison_retrievals <= result.total_queries
        if result.poison_retrievals > 0:
            assert result.mean_poison_rank is not None
            assert result.mean_poison_rank >= 1.0

    def test_validate_adhoc_model_resolves(self) -> None:
        m = resolve_model("some-org/some-model")
        assert m.id == "some-org/some-model"
        assert m.name == "some-org/some-model"
        assert m.dimensions is None

    def test_validate_empty_queries(
        self, corpus: list[CorpusDocument], poison: list[CorpusDocument]
    ) -> None:
        result = validate_retrieval(
            corpus_docs=corpus,
            poison_docs=poison,
            queries=[],
            model_id="minilm-l6",
        )
        assert result.total_queries == 0
        assert result.retrieval_rate == 0.0
        assert result.mean_poison_rank is None
