"""Tests for chain loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from q_ai.chain.loader import (
    ChainValidationError,
    discover_chains,
    load_all_chains,
    load_chain,
)
from q_ai.chain.models import ChainCategory


class TestLoadChain:
    """Tests for load_chain()."""

    def test_load_chain_valid(self, valid_chain_yaml: Path) -> None:
        """Load a valid YAML file, verify ChainDefinition fields."""
        chain = load_chain(valid_chain_yaml)
        assert chain.id == "test-chain"
        assert chain.name == "Test Chain"
        assert chain.category == ChainCategory.RAG_PIPELINE
        assert len(chain.steps) == 2
        assert chain.steps[0].id == "step-one"
        assert chain.steps[0].module == "inject"
        assert chain.steps[0].technique == "description_poisoning"
        assert chain.steps[0].trust_boundary == "agent-to-tool"
        assert chain.steps[0].on_success == "step-two"
        assert chain.steps[0].on_failure == "abort"
        assert chain.steps[1].terminal is True

    def test_load_chain_missing_required_field(self, tmp_path: Path) -> None:
        """Omit steps, expect ChainValidationError."""
        p = tmp_path / "no_steps.yaml"
        p.write_text(
            "id: x\nname: X\ncategory: rag_pipeline\ndescription: X\n",
            encoding="utf-8",
        )
        with pytest.raises(ChainValidationError, match="steps"):
            load_chain(p)

    def test_load_chain_invalid_category(self, tmp_path: Path) -> None:
        """Bad category value raises ChainValidationError."""
        content = """\
id: x
name: X
category: not_a_category
description: X
steps:
  - id: s1
    name: S
    module: inject
    technique: output_injection
    terminal: true
"""
        p = tmp_path / "bad_cat.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ChainValidationError, match="category"):
            load_chain(p)

    def test_load_chain_duplicate_step_ids(self, tmp_path: Path) -> None:
        """Two steps with same id raises ChainValidationError."""
        content = """\
id: x
name: X
category: rag_pipeline
description: X
steps:
  - id: dupe
    name: A
    module: inject
    technique: output_injection
  - id: dupe
    name: B
    module: inject
    technique: output_injection
    terminal: true
"""
        p = tmp_path / "dupe.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ChainValidationError, match=r"[Dd]uplicate"):
            load_chain(p)


class TestDiscoverChains:
    """Tests for discover_chains() and load_all_chains()."""

    def test_discover_chains_finds_builtins(self) -> None:
        """Built-in templates directory has 3 files."""
        paths = discover_chains()
        assert len(paths) == 3
        names = {p.stem for p in paths}
        assert "rag_trust_escalation" in names
        assert "mcp_server_compromise" in names
        assert "delegation_hijack" in names

    def test_load_all_chains(self) -> None:
        """All 3 built-in templates load successfully."""
        chains = load_all_chains()
        assert len(chains) == 3
        ids = {c.id for c in chains}
        assert ids == {"rag-trust-escalation", "mcp-server-compromise", "delegation-hijack"}


class TestChainStepRelevantCategories:
    """Tests for relevant_categories loading on chain steps."""

    def test_builtin_chains_have_relevant_categories(self) -> None:
        """All steps in built-in chains expose relevant_categories as list[str]."""
        chains = load_all_chains()
        for chain in chains:
            for step in chain.steps:
                assert isinstance(step.relevant_categories, list), (
                    f"{chain.id}/{step.id} missing relevant_categories"
                )
                assert all(isinstance(cat, str) for cat in step.relevant_categories), (
                    f"{chain.id}/{step.id} has non-string category values"
                )

    def test_relevant_categories_from_yaml(self, tmp_path: Path) -> None:
        """relevant_categories are read from step entries."""
        content = """\
id: test-chain
name: Test Chain
category: rag_pipeline
description: Test.
steps:
  - id: step-one
    name: First step
    module: inject
    technique: description_poisoning
    relevant_categories:
      - tool_poisoning
      - prompt_injection
    terminal: true
"""
        p = tmp_path / "test_chain.yaml"
        p.write_text(content, encoding="utf-8")
        chain = load_chain(p)
        assert chain.steps[0].relevant_categories == ["tool_poisoning", "prompt_injection"]

    def test_relevant_categories_defaults_empty(self, valid_chain_yaml: Path) -> None:
        """Steps without relevant_categories default to empty list."""
        chain = load_chain(valid_chain_yaml)
        # The conftest fixture doesn't include relevant_categories
        for step in chain.steps:
            assert step.relevant_categories == []

    def test_relevant_categories_invalid_type_raises(self, tmp_path: Path) -> None:
        """Non-list relevant_categories raises ChainValidationError."""
        content = """\
id: test-chain
name: Test Chain
category: rag_pipeline
description: Test.
steps:
  - id: step-one
    name: First step
    module: inject
    technique: description_poisoning
    relevant_categories: "not_a_list"
    terminal: true
"""
        p = tmp_path / "bad_cats.yaml"
        p.write_text(content, encoding="utf-8")
        with pytest.raises(ChainValidationError, match="relevant_categories"):
            load_chain(p)
