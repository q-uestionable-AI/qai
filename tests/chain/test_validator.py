"""Tests for chain validator."""

from __future__ import annotations

from q_ai.chain.models import ChainCategory, ChainDefinition, ChainStep
from q_ai.chain.validator import validate_chain


def _make_chain(steps: list[ChainStep], **kwargs: object) -> ChainDefinition:
    """Helper to build a ChainDefinition with defaults."""
    defaults = {
        "id": "test",
        "name": "Test",
        "category": ChainCategory.RAG_PIPELINE,
        "description": "Test chain",
    }
    defaults.update(kwargs)
    return ChainDefinition(steps=steps, **defaults)  # type: ignore[arg-type]


class TestModuleValidation:
    """Module and technique reference validation."""

    def test_valid_chain_no_errors(self) -> None:
        """Built-in-style chain with valid refs produces no errors."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="description_poisoning",
                on_success="s2",
            ),
            ChainStep(
                id="s2",
                name="S2",
                module="inject",
                technique="output_injection",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert errors == []

    def test_invalid_module_reference(self) -> None:
        """Step with module='nonexistent' produces an error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="nonexistent",
                technique="x",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert len(errors) == 1
        assert errors[0].field == "module"

    def test_invalid_audit_technique(self) -> None:
        """module=audit with fake technique produces an error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="audit",
                technique="fake_scanner",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any(e.field == "technique" for e in errors)

    def test_invalid_inject_technique(self) -> None:
        """module=inject with fake technique produces an error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="fake_technique",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any(e.field == "technique" for e in errors)

    def test_valid_audit_technique(self) -> None:
        """module=audit with real scanner name produces no technique error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="audit",
                technique="injection",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert not any(e.field == "technique" for e in errors)

    def test_valid_inject_technique(self) -> None:
        """module=inject with real technique produces no technique error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="description_poisoning",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert errors == []

    def test_valid_ipi_module(self) -> None:
        """module=ipi with valid format technique produces no errors."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="ipi",
                technique="pdf",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert errors == []

    def test_invalid_ipi_technique(self) -> None:
        """module=ipi with invalid format produces a technique error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="ipi",
                technique="not_a_format",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any(e.field == "technique" for e in errors)

    def test_valid_cxp_module(self) -> None:
        """module=cxp accepts any technique (runtime-validated)."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="cxp",
                technique="cursorrules",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert not any(e.field == "technique" for e in errors)

    def test_valid_rxp_module(self) -> None:
        """module=rxp accepts any technique (runtime-validated)."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="rxp",
                technique="minilm-l6",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert not any(e.field == "technique" for e in errors)

    def test_proxy_module_rejected(self) -> None:
        """module=proxy is rejected — proxy is background, not a chain step."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="proxy",
                technique="background",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any(e.field == "module" for e in errors)

    def test_unknown_module_rejected(self) -> None:
        """module='bogus' is rejected with clear error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="bogus",
                technique="x",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        module_errors = [e for e in errors if e.field == "module"]
        assert len(module_errors) == 1
        assert "bogus" in module_errors[0].message
        assert "Valid modules" in module_errors[0].message


class TestGraphValidation:
    """Step graph reference, cycle, and reachability validation."""

    def test_on_success_references_missing_step(self) -> None:
        """on_success points to nonexistent step ID."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="output_injection",
                on_success="nonexistent",
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any("nonexistent" in e.message for e in errors)

    def test_cycle_detection(self) -> None:
        """Step A on_success->B, step B on_success->A produces cycle error."""
        steps = [
            ChainStep(
                id="a",
                name="A",
                module="inject",
                technique="output_injection",
                on_success="b",
            ),
            ChainStep(
                id="b",
                name="B",
                module="inject",
                technique="output_injection",
                on_success="a",
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any("cycle" in e.message.lower() for e in errors)

    def test_unreachable_step(self) -> None:
        """Step exists but no path from first step reaches it."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="output_injection",
                terminal=True,
            ),
            ChainStep(
                id="orphan",
                name="Orphan",
                module="inject",
                technique="output_injection",
                terminal=True,
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any("orphan" in e.message.lower() for e in errors)

    def test_no_terminal_step(self) -> None:
        """No step is terminal and all have on_success — expect error."""
        steps = [
            ChainStep(
                id="s1",
                name="S1",
                module="inject",
                technique="output_injection",
                on_success="s2",
            ),
            ChainStep(
                id="s2",
                name="S2",
                module="inject",
                technique="output_injection",
                on_success="s1",
            ),
        ]
        errors = validate_chain(_make_chain(steps))
        assert any("terminal" in e.message.lower() for e in errors)
