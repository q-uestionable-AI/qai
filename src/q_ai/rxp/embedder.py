"""Embedding abstraction over sentence-transformers."""

from __future__ import annotations

import functools

import numpy as np
from sentence_transformers import SentenceTransformer


class Embedder:
    """Loads a sentence-transformers model and encodes text to embeddings.

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


@functools.lru_cache(maxsize=8)
def get_embedder(model_name: str) -> Embedder:
    """Get or create a cached Embedder for the given model."""
    return Embedder(model_name)
