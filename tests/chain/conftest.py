"""Fixtures for chain testing."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def valid_chain_yaml(tmp_path: Path) -> Path:
    """Write a valid minimal chain YAML file, return its path."""
    content = """\
id: test-chain
name: Test Chain
category: rag_pipeline
description: A test chain for validation.
steps:
  - id: step-one
    name: First step
    module: inject
    technique: description_poisoning
    trust_boundary: agent-to-tool
    on_success: step-two
    on_failure: abort

  - id: step-two
    name: Second step
    module: inject
    technique: output_injection
    terminal: true
"""
    p = tmp_path / "valid_chain.yaml"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def invalid_chain_yaml(tmp_path: Path) -> Path:
    """Write a chain with bad technique reference, return its path."""
    content = """\
id: bad-chain
name: Bad Chain
category: rag_pipeline
description: A chain with an invalid technique.
steps:
  - id: step-one
    name: First step
    module: inject
    technique: nonexistent_technique
    terminal: true
"""
    p = tmp_path / "invalid_chain.yaml"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def builtin_template_path() -> Path:
    """Return path to one of the built-in YAML template files."""
    import q_ai.chain.templates as templates_pkg

    return Path(templates_pkg.__file__).parent / "rag_trust_escalation.yaml"
