"""Tests for RXP embedding abstraction."""

from __future__ import annotations

import pytest

st = pytest.importorskip("sentence_transformers")

from q_ai.rxp.embedder import get_embedder  # noqa: E402

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class TestEmbedder:
    """Tests for the Embedder class (requires sentence-transformers)."""

    def test_encode_returns_vectors(self) -> None:
        embedder = get_embedder(MODEL_NAME)
        vectors = embedder.encode(["hello world", "test document"])
        assert len(vectors) == 2
        assert all(isinstance(v, list) for v in vectors)
        assert all(isinstance(x, float) for x in vectors[0])

    def test_encode_dimensions_match_model(self) -> None:
        embedder = get_embedder(MODEL_NAME)
        vectors = embedder.encode(["test text"])
        assert len(vectors[0]) == 384

    def test_similarity_self(self) -> None:
        embedder = get_embedder(MODEL_NAME)
        vectors = embedder.encode(["remote work policy"])
        score = embedder.similarity(vectors[0], vectors)[0]
        assert score == pytest.approx(1.0, abs=0.01)

    def test_similarity_related_texts(self) -> None:
        embedder = get_embedder(MODEL_NAME)
        query_vec = embedder.encode(["remote work policy"])[0]
        candidate_vecs = embedder.encode(["working from home guidelines", "expense report filing"])
        scores = embedder.similarity(query_vec, candidate_vecs)
        assert scores[0] > scores[1], "Related text should score higher"

    def test_get_embedder_caching(self) -> None:
        e1 = get_embedder(MODEL_NAME)
        e2 = get_embedder(MODEL_NAME)
        assert e1 is e2
