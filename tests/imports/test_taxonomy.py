"""Tests for taxonomy bridge resolution."""

from q_ai.imports.taxonomy import resolve_bridge


def test_direct_bridge_llm01() -> None:
    bridge = resolve_bridge("owasp_llm_top10", "LLM01")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "direct"


def test_adjacent_bridge_llm02() -> None:
    bridge = resolve_bridge("owasp_llm_top10", "LLM02")
    assert bridge.qai_category == "token_exposure"
    assert bridge.confidence == "adjacent"


def test_adjacent_bridge_llm06() -> None:
    bridge = resolve_bridge("owasp_llm_top10", "LLM06")
    assert bridge.qai_category == "permissions"
    assert bridge.confidence == "adjacent"


def test_no_bridge_llm03() -> None:
    bridge = resolve_bridge("owasp_llm_top10", "LLM03")
    assert bridge.qai_category is None
    assert bridge.confidence == "none"


def test_unknown_framework() -> None:
    bridge = resolve_bridge("nonexistent_framework", "X01")
    assert bridge.qai_category is None
    assert bridge.confidence == "none"
    assert bridge.external_framework == "nonexistent_framework"
    assert bridge.external_id == "X01"


def test_unknown_id_in_known_framework() -> None:
    bridge = resolve_bridge("owasp_llm_top10", "LLM99")
    assert bridge.qai_category is None
    assert bridge.confidence == "none"
