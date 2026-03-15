"""Embedding abstraction over sentence-transformers."""

from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer


class Embedder:
    """Loads a sentence-transformers model and encodes text to embeddings.

    Caches loaded models in memory. Thread-safe for read-only use.

    Args:
        model_name: Full sentence-transformers model name.
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to embedding vectors.

        Args:
            texts: Texts to encode.

        Returns:
            List of embedding vectors (each a list of floats).
        """
        embeddings = self._model.encode(texts, convert_to_numpy=True)
        return [row.tolist() for row in embeddings]

    def similarity(self, query: list[float], candidates: list[list[float]]) -> list[float]:
        """Compute cosine similarity between query and candidates.

        Args:
            query: Query embedding vector.
            candidates: Candidate embedding vectors.

        Returns:
            Cosine similarity scores, same order as candidates.
        """
        q = np.array(query, dtype=np.float32)
        c = np.array(candidates, dtype=np.float32)
        # Normalize
        q_norm = q / (np.linalg.norm(q) + 1e-10)
        c_norms = c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-10)
        scores = c_norms @ q_norm
        result: list[float] = scores.tolist()
        return result


_model_cache: dict[str, Embedder] = {}


def get_embedder(model_name: str) -> Embedder:
    """Get or create a cached Embedder for the given model."""
    if model_name not in _model_cache:
        _model_cache[model_name] = Embedder(model_name)
    return _model_cache[model_name]
