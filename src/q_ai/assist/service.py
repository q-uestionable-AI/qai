"""Assistant service — orchestrates knowledge retrieval, prompt assembly, and LLM chat.

Entry point for the qai assistant. Accepts a query, optional context, and
conversation history, retrieves relevant knowledge, assembles the prompt,
calls the LLM, and returns the response.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from litellm import acompletion  # type: ignore[import-untyped]

from q_ai.assist.knowledge import (
    CHARS_PER_TOKEN,
    DEFAULT_EMBEDDING_MODEL,
    KnowledgeBase,
)
from q_ai.assist.prompt import (
    assemble_messages,
    budget_to_chunk_count,
    compute_retrieval_budget,
)
from q_ai.core.config import get_credential, resolve
from q_ai.core.llm import parse_model_string

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


class AssistantNotConfiguredError(Exception):
    """Raised when the assistant provider/model is not configured."""


def _resolve_model_string() -> str:
    """Resolve the assistant model string from configuration.

    Returns:
        litellm model string in "provider/model" format.

    Raises:
        AssistantNotConfiguredError: If provider or model is not set.
    """
    provider_val, _ = resolve("assist.provider", env_var="QAI_ASSIST_PROVIDER")
    model_val, _ = resolve("assist.model", env_var="QAI_ASSIST_MODEL")

    if not provider_val or not model_val:
        raise AssistantNotConfiguredError(
            "Assistant not configured. Set your provider and model:\n"
            "  qai config set assist.provider ollama && "
            "qai config set assist.model llama3.1"
        )

    return f"{provider_val}/{model_val}"


def _resolve_embedding_model() -> str:
    """Resolve the embedding model name from configuration.

    Returns:
        Sentence-transformers model name.
    """
    val, _ = resolve("assist.embedding_model", env_var="QAI_ASSIST_EMBEDDING_MODEL")
    return val or DEFAULT_EMBEDDING_MODEL


def _resolve_knowledge_dir() -> Path:
    """Resolve the user knowledge directory from configuration.

    Returns:
        Path to the user knowledge directory.
    """
    val, _ = resolve("assist.knowledge_dir", env_var="QAI_ASSIST_KNOWLEDGE_DIR")
    if val:
        return Path(val).expanduser()
    return Path.home() / ".qai" / "knowledge"


def _ensure_credentials(model_string: str) -> None:
    """Ensure API credentials are available for the provider.

    Ollama and other local providers don't need credentials.
    For cloud providers, checks that a credential exists.

    Args:
        model_string: litellm model string.

    Raises:
        AssistantNotConfiguredError: If credentials are missing for cloud providers.
    """
    provider, _ = parse_model_string(model_string)
    local_providers = {"ollama", "lmstudio", "custom"}
    if provider in local_providers:
        return

    credential = get_credential(provider)
    if not credential:
        raise AssistantNotConfiguredError(
            f"No API key found for provider '{provider}'. "
            f"Set it via: qai config set-credential {provider}"
        )


# ---------------------------------------------------------------------------
# Knowledge base singleton
# ---------------------------------------------------------------------------

_kb_instance: KnowledgeBase | None = None


def _get_knowledge_base() -> KnowledgeBase:
    """Get or create the singleton KnowledgeBase instance.

    Returns:
        Initialized KnowledgeBase.
    """
    global _kb_instance  # noqa: PLW0603
    if _kb_instance is None:
        _kb_instance = KnowledgeBase(
            embedding_model=_resolve_embedding_model(),
            knowledge_dir=_resolve_knowledge_dir(),
        )
    return _kb_instance


def reset_knowledge_base() -> None:
    """Reset the cached KnowledgeBase instance."""
    global _kb_instance  # noqa: PLW0603
    _kb_instance = None


# ---------------------------------------------------------------------------
# Core service functions
# ---------------------------------------------------------------------------


def reindex(force: bool = True) -> None:
    """Reindex the knowledge base.

    Args:
        force: If True, reindex everything regardless of changes.
    """
    kb = _get_knowledge_base()
    kb.ensure_indexed(force=force)


async def chat(
    query: str,
    scan_context: str = "",
    history: list[dict[str, str]] | None = None,
) -> str:
    """Send a query to the assistant and return the complete response.

    Args:
        query: User's question.
        scan_context: Optional untrusted scan-derived content.
        history: Optional conversation history.

    Returns:
        Assistant's response text.

    Raises:
        AssistantNotConfiguredError: If provider/model not configured.
    """
    model_string = _resolve_model_string()
    _ensure_credentials(model_string)

    kb = _get_knowledge_base()
    kb.ensure_indexed()

    # Compute retrieval budget
    system_tokens = 800  # Base system prompt estimate
    scan_tokens = len(scan_context) // CHARS_PER_TOKEN if scan_context else 0
    history_tokens = sum(len(m.get("content", "")) // CHARS_PER_TOKEN for m in (history or []))

    budget = compute_retrieval_budget(model_string, system_tokens, scan_tokens, history_tokens)
    chunk_count = budget_to_chunk_count(budget)

    # Retrieve knowledge
    results = kb.retrieve(query, max_chunks=chunk_count)

    # Assemble messages
    messages = assemble_messages(
        query=query,
        model=model_string,
        retrieval_results=results,
        scan_context=scan_context,
        history=history,
    )

    # Call LLM
    response = await acompletion(  # type: ignore[no-untyped-call]
        model=model_string,
        messages=messages,
        timeout=120.0,
    )

    choice = response.choices[0]  # type: ignore[union-attr]
    return choice.message.content or ""  # type: ignore[union-attr]


async def chat_stream(
    query: str,
    scan_context: str = "",
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Send a query and stream the response token by token.

    Args:
        query: User's question.
        scan_context: Optional untrusted scan-derived content.
        history: Optional conversation history.

    Yields:
        Response text chunks as they arrive.

    Raises:
        AssistantNotConfiguredError: If provider/model not configured.
    """
    model_string = _resolve_model_string()
    _ensure_credentials(model_string)

    kb = _get_knowledge_base()
    kb.ensure_indexed()

    # Compute retrieval budget
    system_tokens = 800
    scan_tokens = len(scan_context) // CHARS_PER_TOKEN if scan_context else 0
    history_tokens = sum(len(m.get("content", "")) // CHARS_PER_TOKEN for m in (history or []))

    budget = compute_retrieval_budget(model_string, system_tokens, scan_tokens, history_tokens)
    chunk_count = budget_to_chunk_count(budget)

    results = kb.retrieve(query, max_chunks=chunk_count)

    messages = assemble_messages(
        query=query,
        model=model_string,
        retrieval_results=results,
        scan_context=scan_context,
        history=history,
    )

    response: Any = await acompletion(  # type: ignore[no-untyped-call]
        model=model_string,
        messages=messages,
        stream=True,
        timeout=120.0,
    )

    async for chunk in response:
        delta = chunk.choices[0].delta  # type: ignore[union-attr]
        content = delta.content  # type: ignore[union-attr]
        if content:
            yield content
