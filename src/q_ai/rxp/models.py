"""Core data models for q-ai RXP."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmbeddingModelConfig:
    """Configuration for an embedding model.

    Attributes:
        id: Short identifier for CLI and reports (e.g., "minilm-l6").
        name: Full model name for sentence-transformers loading
            (e.g., "sentence-transformers/all-MiniLM-L6-v2").
        dimensions: Embedding vector dimensions.
        description: Human-readable description.
    """

    id: str
    name: str
    dimensions: int | None = None
    description: str = ""


@dataclass
class CorpusDocument:
    """A document in a test corpus.

    Attributes:
        id: Unique document identifier.
        text: Document text content.
        source: Origin file path or label.
        is_poison: Whether this is an adversarial document.
        metadata: Additional metadata for ChromaDB storage.
    """

    id: str
    text: str
    source: str
    is_poison: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class RetrievalHit:
    """A single retrieval result from a query.

    Attributes:
        document_id: ID of the retrieved document.
        rank: Position in results (1-based).
        distance: Distance score from the query embedding.
        is_poison: Whether the retrieved document is adversarial.
    """

    document_id: str
    rank: int
    distance: float
    is_poison: bool


@dataclass
class QueryResult:
    """Results of one query against one model.

    Attributes:
        query: The query text.
        model_id: Embedding model used.
        top_k: Number of results requested.
        hits: Retrieved documents in rank order.
        poison_retrieved: Whether any poison document appears in hits.
        poison_rank: Rank of highest poison document, or None.
    """

    query: str
    model_id: str
    top_k: int
    hits: list[RetrievalHit]
    poison_retrieved: bool
    poison_rank: int | None


@dataclass
class ValidationResult:
    """Aggregate retrieval validation across queries and models.

    Attributes:
        model_id: Embedding model tested.
        total_queries: Number of queries tested.
        poison_retrievals: Number of queries where poison was in top-k.
        retrieval_rate: Fraction of queries where poison was retrieved.
        mean_poison_rank: Average rank of poison doc when retrieved.
        query_results: Per-query detail.
    """

    model_id: str
    total_queries: int
    poison_retrievals: int
    retrieval_rate: float
    mean_poison_rank: float | None
    query_results: list[QueryResult]

    def _build_interpret_prompt(self) -> str:
        """Assemble an AI-evaluation prompt from RXP validation results.

        The prompt is findings-driven only: embedding model, query count,
        retrieval rate, mean rank. Tool identity excluded.

        Returns:
            Prompt string ready for embedding in JSON output.
        """
        if self.total_queries == 0:
            rate_str = "N/A retrieval rate (0 queries)"
        else:
            rate_pct = f"{self.retrieval_rate:.0%}"
            rate_str = (
                f"{rate_pct} retrieval rate ({self.poison_retrievals}/{self.total_queries} queries)"
            )
        rank_str = f"{self.mean_poison_rank:.1f}" if self.mean_poison_rank is not None else "N/A"
        return (
            f"Retrieval poisoning validation against {self.model_id}: "
            f"{rate_str}, "
            f"mean poison rank {rank_str}. "
            "Assess poisoning confidence and recommend document and "
            "query optimization to improve retrieval rank."
        )

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        return {
            "prompt": self._build_interpret_prompt(),
            "model_id": self.model_id,
            "total_queries": self.total_queries,
            "poison_retrievals": self.poison_retrievals,
            "retrieval_rate": self.retrieval_rate,
            "mean_poison_rank": self.mean_poison_rank,
            "query_results": [
                {
                    "query": qr.query,
                    "model_id": qr.model_id,
                    "top_k": qr.top_k,
                    "poison_retrieved": qr.poison_retrieved,
                    "poison_rank": qr.poison_rank,
                    "hits": [
                        {
                            "document_id": h.document_id,
                            "rank": h.rank,
                            "distance": h.distance,
                            "is_poison": h.is_poison,
                        }
                        for h in qr.hits
                    ],
                }
                for qr in self.query_results
            ],
        }


@dataclass
class DomainProfile:
    """A target domain with corpus and queries for retrieval testing.

    Attributes:
        id: Short identifier (e.g., "hr-policy").
        name: Human-readable name.
        description: What this domain represents.
        corpus_dir: Directory name under the built-in profiles path.
        queries: Target queries users would ask in this domain.
    """

    id: str
    name: str
    description: str
    corpus_dir: str
    queries: list[str]
