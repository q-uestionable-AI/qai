"""Tests for RXP embedding model registry."""

from __future__ import annotations

from q_ai.rxp.models import EmbeddingModelConfig
from q_ai.rxp.registry import get_model, list_models, resolve_model


class TestRegistry:
    """Tests for the embedding model registry."""

    def test_list_models_returns_three(self) -> None:
        models = list_models()
        assert len(models) == 3

    def test_list_models_returns_config_instances(self) -> None:
        models = list_models()
        for m in models:
            assert isinstance(m, EmbeddingModelConfig)

    def test_get_minilm_l6(self) -> None:
        m = get_model("minilm-l6")
        assert m is not None
        assert m.name == "sentence-transformers/all-MiniLM-L6-v2"
        assert m.dimensions == 384

    def test_get_bge_small(self) -> None:
        m = get_model("bge-small")
        assert m is not None
        assert m.name == "BAAI/bge-small-en-v1.5"
        assert m.dimensions == 384

    def test_get_unknown_returns_none(self) -> None:
        assert get_model("nonexistent-model") is None

    def test_resolve_registry_model(self) -> None:
        m = resolve_model("minilm-l6")
        assert m.id == "minilm-l6"
        assert m.name == "sentence-transformers/all-MiniLM-L6-v2"
        assert m.dimensions == 384

    def test_resolve_arbitrary_hf_name(self) -> None:
        m = resolve_model("BAAI/bge-m3")
        assert m.id == "BAAI/bge-m3"
        assert m.name == "BAAI/bge-m3"
        assert m.dimensions is None
        assert m.description == ""
