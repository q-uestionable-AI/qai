"""Taxonomy bridge — maps external framework IDs to qai audit categories."""

from __future__ import annotations

from q_ai.imports.models import TaxonomyBridge

_OWASP_LLM_FRAMEWORK = "owasp_llm_top10"

# Mapping: external_id -> (qai_category | None, confidence)
# Only entries with a meaningful infrastructure-level equivalent get "direct"/"adjacent".
# Most LLM Top 10 entries are model-level concerns with no qai equivalent.
_OWASP_LLM_BRIDGES: dict[str, tuple[str | None, str]] = {
    "LLM01": ("prompt_injection", "direct"),
    "LLM02": ("token_exposure", "adjacent"),
    "LLM03": (None, "none"),  # Training Data Poisoning
    "LLM04": (None, "none"),  # Model Denial of Service
    "LLM05": (None, "none"),  # Supply Chain Vulnerabilities (model-level)
    "LLM06": ("permissions", "adjacent"),  # Excessive Agency
    "LLM07": (None, "none"),  # Insecure Plugin Design
    "LLM08": (None, "none"),  # Excessive Autonomy
    "LLM09": (None, "none"),  # Overreliance
    "LLM10": (None, "none"),  # Model Theft
}

# Registry keyed by framework name for extensibility.
_BRIDGE_REGISTRY: dict[str, dict[str, tuple[str | None, str]]] = {
    _OWASP_LLM_FRAMEWORK: _OWASP_LLM_BRIDGES,
}


def resolve_bridge(external_framework: str, external_id: str) -> TaxonomyBridge:
    """Look up a taxonomy bridge for an external framework ID.

    Args:
        external_framework: Framework name, e.g. ``"owasp_llm_top10"``.
        external_id: Identifier within the framework, e.g. ``"LLM01"``.

    Returns:
        A :class:`TaxonomyBridge` with mapping details. If the framework or ID
        is unknown the bridge will have ``confidence="none"`` and
        ``qai_category=None``.
    """
    framework_map = _BRIDGE_REGISTRY.get(external_framework, {})
    qai_category, confidence = framework_map.get(external_id, (None, "none"))
    return TaxonomyBridge(
        external_framework=external_framework,
        external_id=external_id,
        qai_category=qai_category,
        confidence=confidence,
    )
