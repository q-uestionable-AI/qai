"""Tests for RXP data models."""

from __future__ import annotations

from q_ai.rxp.models import (
    CorpusDocument,
    EmbeddingModelConfig,
    QueryResult,
    RetrievalHit,
    ValidationResult,
)


class TestEmbeddingModelConfig:
    """Tests for EmbeddingModelConfig dataclass."""

    def test_create_embedding_model_config(self) -> None:
        config = EmbeddingModelConfig(
            id="test-model",
            name="org/test-model-v1",
            dimensions=384,
            description="Test model",
        )
        assert config.id == "test-model"
        assert config.name == "org/test-model-v1"
        assert config.dimensions == 384
        assert config.description == "Test model"


class TestCorpusDocument:
    """Tests for CorpusDocument dataclass."""

    def test_create_corpus_document(self) -> None:
        doc = CorpusDocument(id="doc-1", text="Hello world", source="test.txt")
        assert doc.id == "doc-1"
        assert doc.is_poison is False
        assert doc.metadata == {}

    def test_create_poison_document(self) -> None:
        doc = CorpusDocument(id="poison-1", text="Malicious", source="poison.txt", is_poison=True)
        assert doc.is_poison is True


class TestRetrievalHit:
    """Tests for RetrievalHit dataclass."""

    def test_create_retrieval_hit(self) -> None:
        hit = RetrievalHit(document_id="doc-1", rank=1, distance=0.25, is_poison=False)
        assert hit.document_id == "doc-1"
        assert hit.rank == 1
        assert hit.distance == 0.25
        assert hit.is_poison is False


class TestQueryResult:
    """Tests for QueryResult dataclass."""

    def test_query_result_poison_tracking(self) -> None:
        hits = [
            RetrievalHit(document_id="doc-1", rank=1, distance=0.1, is_poison=False),
            RetrievalHit(document_id="poison-1", rank=2, distance=0.3, is_poison=True),
        ]
        qr = QueryResult(
            query="test query",
            model_id="test-model",
            top_k=5,
            hits=hits,
            poison_retrieved=True,
            poison_rank=2,
        )
        assert qr.poison_retrieved is True
        assert qr.poison_rank == 2

    def test_query_result_no_poison(self) -> None:
        hits = [
            RetrievalHit(document_id="doc-1", rank=1, distance=0.1, is_poison=False),
        ]
        qr = QueryResult(
            query="test query",
            model_id="test-model",
            top_k=5,
            hits=hits,
            poison_retrieved=False,
            poison_rank=None,
        )
        assert qr.poison_retrieved is False
        assert qr.poison_rank is None


class TestValidationResultInterpretPrompt:
    """Tests for ValidationResult._build_interpret_prompt()."""

    def test_interpret_prompt_content(self) -> None:
        vr = ValidationResult(
            model_id="minilm-l6",
            total_queries=10,
            poison_retrievals=7,
            retrieval_rate=0.7,
            mean_poison_rank=2.5,
            query_results=[],
        )
        prompt = vr._build_interpret_prompt()
        assert "minilm-l6" in prompt
        assert "70%" in prompt
        assert "7/10" in prompt
        assert "2.5" in prompt
        assert "Assess poisoning confidence" in prompt
        # Must not contain tool identity
        for forbidden in ("CounterSignal", "countersignal", "CXP", "IPI", "RXP"):
            assert forbidden not in prompt

    def test_interpret_prompt_no_rank(self) -> None:
        vr = ValidationResult(
            model_id="minilm-l6",
            total_queries=5,
            poison_retrievals=0,
            retrieval_rate=0.0,
            mean_poison_rank=None,
            query_results=[],
        )
        prompt = vr._build_interpret_prompt()
        assert "N/A" in prompt
        assert "0%" in prompt

    def test_prompt_in_to_dict(self) -> None:
        vr = ValidationResult(
            model_id="minilm-l6",
            total_queries=1,
            poison_retrievals=1,
            retrieval_rate=1.0,
            mean_poison_rank=1.0,
            query_results=[],
        )
        d = vr.to_dict()
        assert "prompt" in d
        assert isinstance(d["prompt"], str)
        assert len(d["prompt"]) > 0
        # prompt should be first key
        assert next(iter(d.keys())) == "prompt"


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_validation_result_to_dict(self) -> None:
        hits = [
            RetrievalHit(document_id="doc-1", rank=1, distance=0.1, is_poison=False),
            RetrievalHit(document_id="poison-1", rank=2, distance=0.3, is_poison=True),
        ]
        qr = QueryResult(
            query="test query",
            model_id="minilm-l6",
            top_k=5,
            hits=hits,
            poison_retrieved=True,
            poison_rank=2,
        )
        vr = ValidationResult(
            model_id="minilm-l6",
            total_queries=1,
            poison_retrievals=1,
            retrieval_rate=1.0,
            mean_poison_rank=2.0,
            query_results=[qr],
        )

        d = vr.to_dict()
        assert d["model_id"] == "minilm-l6"
        assert d["total_queries"] == 1
        assert d["retrieval_rate"] == 1.0
        assert d["mean_poison_rank"] == 2.0
        assert len(d["query_results"]) == 1
        assert d["query_results"][0]["poison_retrieved"] is True
        assert len(d["query_results"][0]["hits"]) == 2
        assert d["query_results"][0]["hits"][1]["is_poison"] is True
