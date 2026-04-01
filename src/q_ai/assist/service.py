"""Assistant service — orchestrates knowledge retrieval, prompt assembly, and LLM chat.

Entry point for the qai assistant. Accepts a query, optional context, and
conversation history, retrieves relevant knowledge, assembles the prompt,
calls the LLM, and returns the response.
"""

from __future__ import annotations

import contextlib
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


def _resolve_base_url() -> str | None:
    """Resolve the assistant base URL from configuration.

    Returns:
        Base URL string if configured, None otherwise.
    """
    val, _ = resolve("assist.base_url", env_var="QAI_ASSIST_BASE_URL")
    return val or None


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


def _resolve_credential(model_string: str) -> str | None:
    """Resolve the API key for the assistant's provider.

    Uses the ``assist.<provider>`` namespace in the keyring so the
    assistant's credential is independent of target-provider keys.
    Falls back to the bare ``<provider>`` key (shared target credential)
    for convenience when only one key is configured.

    Local providers (ollama, lmstudio, custom) may return ``None``
    without error — they don't require credentials.

    Args:
        model_string: litellm model string.

    Returns:
        API key string, or None for local providers.

    Raises:
        AssistantNotConfiguredError: If credentials are missing for cloud providers.
    """
    provider, _ = parse_model_string(model_string)
    local_providers = {"ollama", "lmstudio", "custom"}
    if provider in local_providers:
        # Local providers: check namespaced key but don't require it.
        try:
            return get_credential(f"assist.{provider}")
        except RuntimeError:
            return None

    # Cloud provider: try namespaced key first, then bare key.
    credential: str | None = None
    with contextlib.suppress(RuntimeError):
        credential = get_credential(f"assist.{provider}")
    if not credential:
        with contextlib.suppress(RuntimeError):
            credential = get_credential(provider)
    if not credential:
        raise AssistantNotConfiguredError(
            f"No API key found for assistant provider '{provider}'. "
            f"Configure it in Settings or via: qai config set-credential assist.{provider}"
        )
    return credential


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


def _retrieval_chunk_count(
    model_string: str,
    scan_context: str,
    history: list[dict[str, str]] | None,
) -> int:
    """Compute how many retrieval chunks fit in the context budget.

    Args:
        model_string: litellm model string.
        scan_context: Untrusted scan-derived content (may be empty).
        history: Conversation history (may be None).

    Returns:
        Number of chunks to retrieve.
    """
    system_tokens = 800  # Base system prompt estimate
    scan_tokens = len(scan_context) // CHARS_PER_TOKEN if scan_context else 0
    history_tokens = sum(len(m.get("content", "")) // CHARS_PER_TOKEN for m in (history or []))
    budget = compute_retrieval_budget(model_string, system_tokens, scan_tokens, history_tokens)
    return budget_to_chunk_count(budget)


def _prepare_messages(
    query: str,
    model_string: str,
    kb: KnowledgeBase,
    scan_context: str,
    history: list[dict[str, str]] | None,
    source: str = "",
) -> list[dict[str, str]]:
    """Retrieve knowledge and assemble the LLM message sequence.

    Args:
        query: User's question.
        model_string: litellm model string.
        kb: Initialized KnowledgeBase.
        scan_context: Untrusted scan-derived content.
        history: Conversation history.
        source: Interaction surface hint (e.g. "web_ui").

    Returns:
        Message list ready for litellm acompletion.
    """
    chunk_count = _retrieval_chunk_count(model_string, scan_context, history)
    results = kb.retrieve(query, max_chunks=chunk_count)
    return assemble_messages(
        query=query,
        model=model_string,
        retrieval_results=results,
        scan_context=scan_context,
        history=history,
        source=source,
    )


async def chat(
    query: str,
    scan_context: str = "",
    history: list[dict[str, str]] | None = None,
    source: str = "",
) -> str:
    """Send a query to the assistant and return the complete response.

    Args:
        query: User's question.
        scan_context: Optional untrusted scan-derived content.
        history: Optional conversation history.
        source: Interaction surface hint (e.g. "web_ui").

    Returns:
        Assistant's response text.

    Raises:
        AssistantNotConfiguredError: If provider/model not configured.
    """
    model_string = _resolve_model_string()
    credential = _resolve_credential(model_string)

    kb = _get_knowledge_base()
    kb.ensure_indexed()

    messages = _prepare_messages(query, model_string, kb, scan_context, history, source=source)

    call_kwargs: dict[str, Any] = {
        "model": model_string,
        "messages": messages,
        "timeout": 120.0,
    }
    base_url = _resolve_base_url()
    if base_url:
        call_kwargs["api_base"] = base_url
    if credential:
        call_kwargs["api_key"] = credential

    response = await acompletion(**call_kwargs)  # type: ignore[no-untyped-call]

    choice = response.choices[0]  # type: ignore[union-attr]
    return choice.message.content or ""  # type: ignore[union-attr]


async def chat_stream(
    query: str,
    scan_context: str = "",
    history: list[dict[str, str]] | None = None,
    source: str = "",
) -> AsyncIterator[str]:
    """Send a query and stream the response token by token.

    Args:
        query: User's question.
        scan_context: Optional untrusted scan-derived content.
        history: Optional conversation history.
        source: Interaction surface hint (e.g. "web_ui").

    Yields:
        Response text chunks as they arrive.

    Raises:
        AssistantNotConfiguredError: If provider/model not configured.
    """
    model_string = _resolve_model_string()
    credential = _resolve_credential(model_string)

    kb = _get_knowledge_base()
    kb.ensure_indexed()

    messages = _prepare_messages(query, model_string, kb, scan_context, history, source=source)

    call_kwargs: dict[str, Any] = {
        "model": model_string,
        "messages": messages,
        "stream": True,
        "timeout": 120.0,
    }
    base_url = _resolve_base_url()
    if base_url:
        call_kwargs["api_base"] = base_url
    if credential:
        call_kwargs["api_key"] = credential

    response: Any = await acompletion(**call_kwargs)  # type: ignore[no-untyped-call]

    async for chunk in response:
        delta = chunk.choices[0].delta  # type: ignore[union-attr]
        content = delta.content  # type: ignore[union-attr]
        if content:
            yield content
