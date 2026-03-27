"""Cross-module validation and graph checking for chain definitions.

Validates ChainDefinition objects against module registries and structural
graph rules (cycle detection, reachability, terminal step existence).
"""

from __future__ import annotations

from dataclasses import dataclass

from q_ai.chain.models import ChainDefinition


@dataclass
class ValidationError:
    """A single validation problem found in a chain definition.

    Attributes:
        step_id: The step where the error occurred, or None for chain-level errors.
        field: The field name that failed validation.
        message: Human-readable description of the problem.
    """

    step_id: str | None
    field: str
    message: str


_VALID_MODULES = {"audit", "inject", "ipi", "cxp", "rxp", "proxy"}


def _validate_module_and_technique(
    chain: ChainDefinition,
    technique_sets: dict[str, set[str] | None],
) -> list[ValidationError]:
    """Validate module and technique references for each step.

    Checks that each step references a known module and that its technique
    is valid for the declared module.

    Args:
        chain: The chain definition to validate.
        technique_sets: Mapping of module name to valid technique set.
            None means any technique is accepted (runtime-validated).

    Returns:
        A list of ValidationError instances for invalid references.
    """
    errors: list[ValidationError] = []

    for step in chain.steps:
        if step.module not in _VALID_MODULES:
            errors.append(
                ValidationError(
                    step_id=step.id,
                    field="module",
                    message=(
                        f"Step '{step.id}' references unknown module '{step.module}'. "
                        f"Valid modules: {sorted(_VALID_MODULES)}"
                    ),
                )
            )
            continue

        valid_techniques = technique_sets.get(step.module)
        # None means accept any technique (runtime-validated or no techniques needed)
        if valid_techniques is None:
            continue

        if step.technique not in valid_techniques:
            errors.append(
                ValidationError(
                    step_id=step.id,
                    field="technique",
                    message=(
                        f"Step '{step.id}' references unknown {step.module} technique "
                        f"'{step.technique}'. "
                        f"Valid techniques: {sorted(valid_techniques)}"
                    ),
                )
            )

    return errors


def _validate_graph_references(
    chain: ChainDefinition,
    valid_step_ids: set[str],
) -> list[ValidationError]:
    """Validate that on_success/on_failure targets reference valid step IDs.

    Checks each step's on_success and on_failure fields. Values of None
    and 'abort' are allowed; anything else must be a valid step ID.

    Args:
        chain: The chain definition to validate.
        valid_step_ids: Set of all valid step IDs in the chain.

    Returns:
        A list of ValidationError instances for invalid references.
    """
    errors: list[ValidationError] = []

    for step in chain.steps:
        for attr, value in (("on_success", step.on_success), ("on_failure", step.on_failure)):
            if value is None:
                continue
            if value == "abort":
                continue
            if value not in valid_step_ids:
                errors.append(
                    ValidationError(
                        step_id=step.id,
                        field=attr,
                        message=(
                            f"Step '{step.id}' {attr} references unknown step '{value}'. "
                            f"Valid step IDs: {sorted(valid_step_ids)}"
                        ),
                    )
                )

    return errors


def validate_chain(chain: ChainDefinition) -> list[ValidationError]:
    """Validate a chain definition against module registries and graph rules.

    Performs the following checks in order:
    1. Module reference — step.module must be 'audit' or 'inject'.
    2. Technique reference — technique must be valid for the declared module.
    3. Step graph references — on_success/on_failure must be valid step IDs or 'abort'.
    4. Cycle detection — on_success/on_failure graph must be acyclic.
    5. Reachability — all steps must be reachable from the first step.
    6. Terminal step — at least one step must be terminal or have no on_success.

    Registries are imported lazily to avoid circular imports.

    Args:
        chain: The chain definition to validate.

    Returns:
        A list of ValidationError instances. An empty list means the chain is valid.
    """
    from q_ai.audit.scanner.registry import list_scanner_names
    from q_ai.inject.models import InjectionTechnique
    from q_ai.ipi.models import Format as IPIFormat

    errors: list[ValidationError] = []

    if not chain.steps:
        return [
            ValidationError(
                step_id=None,
                field="steps",
                message="Chain must contain at least one step.",
            )
        ]

    valid_step_ids = {step.id for step in chain.steps}

    # Build technique sets per module.
    # None = accept any technique (validated at runtime).
    technique_sets: dict[str, set[str] | None] = {
        "audit": set(list_scanner_names()),
        "inject": {t.value for t in InjectionTechnique},
        "ipi": {f.value for f in IPIFormat},
        "cxp": None,  # format IDs validated at build time
        "rxp": None,  # model availability is runtime-dependent
        "proxy": None,  # proxy steps are background config, no technique
    }

    # --- Check 1 & 2: module and technique references ---
    errors.extend(_validate_module_and_technique(chain, technique_sets))

    # --- Check 3: graph reference validity ---
    errors.extend(_validate_graph_references(chain, valid_step_ids))

    # --- Check 4: cycle detection ---
    cycle_errors = _detect_cycles(chain, valid_step_ids)
    errors.extend(cycle_errors)

    # --- Check 5: reachability ---
    reachability_errors = _check_reachability(chain, valid_step_ids)
    errors.extend(reachability_errors)

    # --- Check 6: terminal step existence ---
    last_index = len(chain.steps) - 1
    has_terminal = any(
        step.terminal or (step.on_success is None and idx == last_index)
        for idx, step in enumerate(chain.steps)
    )
    if not has_terminal:
        errors.append(
            ValidationError(
                step_id=None,
                field="terminal",
                message=(
                    "Chain has no terminal step. At least one step must have "
                    "terminal=True or no on_success defined."
                ),
            )
        )

    return errors


def _build_adjacency(chain: ChainDefinition, valid_step_ids: set[str]) -> dict[str, list[str]]:
    """Build an adjacency list for the step graph.

    Includes on_success, on_failure, and implicit list-order successors.
    Only includes edges that point to valid step IDs (not 'abort' or unknown).

    Args:
        chain: The chain definition.
        valid_step_ids: Set of all valid step IDs in the chain.

    Returns:
        A mapping from step ID to a list of reachable step IDs.
    """
    step_index = {step.id: i for i, step in enumerate(chain.steps)}
    adjacency: dict[str, list[str]] = {step.id: [] for step in chain.steps}

    for step in chain.steps:
        neighbors: list[str] = []

        # Explicit on_success
        if step.on_success is not None and step.on_success in valid_step_ids:
            neighbors.append(step.on_success)
        elif step.on_success is None and not step.terminal:
            # Implicit successor: next step in list order
            idx = step_index[step.id]
            if idx + 1 < len(chain.steps):
                neighbors.append(chain.steps[idx + 1].id)

        # Explicit on_failure
        if step.on_failure is not None and step.on_failure in valid_step_ids:
            neighbors.append(step.on_failure)

        adjacency[step.id] = neighbors

    return adjacency


def _detect_cycles(chain: ChainDefinition, valid_step_ids: set[str]) -> list[ValidationError]:
    """Detect cycles in the step graph using DFS.

    Args:
        chain: The chain definition to inspect.
        valid_step_ids: Set of all valid step IDs.

    Returns:
        List of ValidationError instances for each detected cycle.
    """
    adjacency = _build_adjacency(chain, valid_step_ids)
    errors: list[ValidationError] = []

    # DFS-based cycle detection; track states: 0=unvisited, 1=in-stack, 2=done
    state: dict[str, int] = dict.fromkeys(valid_step_ids, 0)
    cycle_reported = False

    def dfs(node: str, path: list[str]) -> bool:
        """Recursive DFS; returns True if a cycle is found."""
        nonlocal cycle_reported
        state[node] = 1
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if state[neighbor] == 1:
                if not cycle_reported:
                    cycle_reported = True
                    errors.append(
                        ValidationError(
                            step_id=None,
                            field="graph",
                            message=(
                                f"Cycle detected in step graph involving step '{neighbor}'. "
                                f"Path: {' -> '.join(path)} -> {neighbor}"
                            ),
                        )
                    )
                return True
            if state[neighbor] == 0 and dfs(neighbor, path):
                return True
        path.pop()
        state[node] = 2
        return False

    for step in chain.steps:
        if state[step.id] == 0:
            dfs(step.id, [])

    return errors


def _check_reachability(chain: ChainDefinition, valid_step_ids: set[str]) -> list[ValidationError]:
    """Check that all steps are reachable from the first step.

    Args:
        chain: The chain definition to inspect.
        valid_step_ids: Set of all valid step IDs.

    Returns:
        List of ValidationError instances for each unreachable step.
    """
    if not chain.steps:
        return []

    adjacency = _build_adjacency(chain, valid_step_ids)
    start = chain.steps[0].id
    visited: set[str] = set()
    queue = [start]

    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        queue.extend(neighbor for neighbor in adjacency.get(node, []) if neighbor not in visited)

    unreachable = valid_step_ids - visited
    errors: list[ValidationError] = [
        ValidationError(
            step_id=step_id,
            field="reachability",
            message=(
                f"Step '{step_id}' is unreachable from the first step "
                f"'{start}' via on_success/on_failure paths."
            ),
        )
        for step_id in sorted(unreachable)
    ]
    return errors
