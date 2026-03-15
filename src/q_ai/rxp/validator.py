"""Retrieval validation engine for RXP."""

from __future__ import annotations

from q_ai.rxp.collection import RetrievalCollection
from q_ai.rxp.embedder import get_embedder
from q_ai.rxp.models import (
    CorpusDocument,
    QueryResult,
    ValidationResult,
)
from q_ai.rxp.registry import resolve_model


def validate_retrieval(
    corpus_docs: list[CorpusDocument],
    poison_docs: list[CorpusDocument],
    queries: list[str],
    model_id: str,
    top_k: int = 5,
) -> ValidationResult:
    """Validate retrieval rank of poison documents against a corpus.

    Ingests corpus + poison docs into a ChromaDB collection,
    runs all queries, scores whether poison docs appear in top-k.

    Args:
        corpus_docs: Legitimate corpus documents.
        poison_docs: Adversarial documents (is_poison=True).
        queries: Target queries to test retrieval against.
        model_id: Embedding model registry ID or HuggingFace model name.
        top_k: Number of retrieval results per query.

    Returns:
        ValidationResult with per-query detail and aggregate stats.
    """
    model_config = resolve_model(model_id)

    embedder = get_embedder(model_config.name)
    collection = RetrievalCollection(name=f"rxp-validate-{model_id}", embedder=embedder)

    # Ingest all documents
    collection.ingest(corpus_docs)
    collection.ingest(poison_docs)

    # Run queries
    query_results: list[QueryResult] = []
    for query_text in queries:
        hits = collection.query(query_text, top_k=top_k)

        poison_hit = next((h for h in hits if h.is_poison), None)
        query_results.append(
            QueryResult(
                query=query_text,
                model_id=model_id,
                top_k=top_k,
                hits=hits,
                poison_retrieved=poison_hit is not None,
                poison_rank=poison_hit.rank if poison_hit else None,
            )
        )

    # Aggregate stats
    total_queries = len(query_results)
    poison_retrievals = sum(1 for qr in query_results if qr.poison_retrieved)
    retrieval_rate = poison_retrievals / total_queries if total_queries > 0 else 0.0

    poison_ranks = [qr.poison_rank for qr in query_results if qr.poison_rank is not None]
    mean_poison_rank = sum(poison_ranks) / len(poison_ranks) if poison_ranks else None

    return ValidationResult(
        model_id=model_id,
        total_queries=total_queries,
        poison_retrievals=poison_retrievals,
        retrieval_rate=retrieval_rate,
        mean_poison_rank=mean_poison_rank,
        query_results=query_results,
    )
