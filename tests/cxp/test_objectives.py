"""Tests for objective registry."""

from __future__ import annotations

from q_ai.cxp.models import Objective
from q_ai.cxp.objectives import get_objective, list_objectives


class TestObjectiveRegistry:
    def test_list_objectives_returns_five(self) -> None:
        objectives = list_objectives()
        assert len(objectives) == 5

    def test_list_objectives_returns_objective_instances(self) -> None:
        objectives = list_objectives()
        for obj in objectives:
            assert isinstance(obj, Objective)

    def test_get_backdoor(self) -> None:
        obj = get_objective("backdoor")
        assert obj is not None
        assert obj.id == "backdoor"
        assert obj.name == "Backdoor Insertion"
        assert "backdoor-hardcoded-cred" in obj.validators
        assert "backdoor-bypass-condition" in obj.validators

    def test_get_exfil(self) -> None:
        obj = get_objective("exfil")
        assert obj is not None
        assert obj.id == "exfil"
        assert obj.name == "Credential Exfiltration"
        assert "exfil-outbound-call" in obj.validators
        assert "exfil-data-collection" in obj.validators

    def test_get_depconfusion(self) -> None:
        obj = get_objective("depconfusion")
        assert obj is not None
        assert obj.id == "depconfusion"
        assert obj.name == "Dependency Confusion"
        assert "depconfusion-registry-override" in obj.validators
        assert "depconfusion-typosquat" in obj.validators

    def test_get_permescalation(self) -> None:
        obj = get_objective("permescalation")
        assert obj is not None
        assert obj.id == "permescalation"
        assert obj.name == "Permission Escalation"
        assert "permescalation-insecure-perms" in obj.validators
        assert "permescalation-elevated-exec" in obj.validators

    def test_get_cmdexec(self) -> None:
        obj = get_objective("cmdexec")
        assert obj is not None
        assert obj.id == "cmdexec"
        assert obj.name == "Command Execution"
        assert "cmdexec-shell-invocation" in obj.validators
        assert "cmdexec-dynamic-eval" in obj.validators

    def test_get_unknown_returns_none(self) -> None:
        assert get_objective("nonexistent") is None
