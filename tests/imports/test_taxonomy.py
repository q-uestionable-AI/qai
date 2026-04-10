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


# --- IPI probe framework tests ---


def test_ipi_probe_instruction_override_direct() -> None:
    bridge = resolve_bridge("ipi_probe", "instruction_override")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "direct"


def test_ipi_probe_delimiter_confusion_direct() -> None:
    bridge = resolve_bridge("ipi_probe", "delimiter_confusion")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "direct"


def test_ipi_probe_task_hijacking_direct() -> None:
    bridge = resolve_bridge("ipi_probe", "task_hijacking")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "direct"


def test_ipi_probe_context_manipulation_adjacent() -> None:
    bridge = resolve_bridge("ipi_probe", "context_manipulation")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "adjacent"


def test_ipi_probe_authority_spoofing_adjacent() -> None:
    bridge = resolve_bridge("ipi_probe", "authority_spoofing")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "adjacent"


def test_ipi_probe_exfil_framing_adjacent() -> None:
    bridge = resolve_bridge("ipi_probe", "exfil_framing")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "adjacent"


def test_ipi_probe_encoding_adjacent() -> None:
    bridge = resolve_bridge("ipi_probe", "encoding")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "adjacent"


def test_ipi_probe_subtle_injection_adjacent() -> None:
    bridge = resolve_bridge("ipi_probe", "subtle_injection")
    assert bridge.qai_category == "prompt_injection"
    assert bridge.confidence == "adjacent"


def test_ipi_probe_unknown_category() -> None:
    bridge = resolve_bridge("ipi_probe", "nonexistent_category")
    assert bridge.qai_category is None
    assert bridge.confidence == "none"
