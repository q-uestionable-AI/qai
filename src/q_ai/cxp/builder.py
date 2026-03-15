"""Repo builder -- assembles poisoned test repositories.

Assembles context files from clean base templates plus modular rules.
Generates a prompt reference companion file alongside the built repo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files as resource_files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from importlib.abc import Traversable

from q_ai.cxp.base_loader import insert_rules, load_base, strip_markers
from q_ai.cxp.formats import get_format
from q_ai.cxp.models import BuildResult, Rule
from q_ai.cxp.prompt_reference import generate_prompt_reference


def _copy_tree(source: Traversable, dest: Path) -> None:
    """Recursively copy a Traversable directory tree to a filesystem path.

    Args:
        source: Source directory (importlib.resources Traversable).
        dest: Destination filesystem path.
    """
    for item in source.iterdir():
        if item.name == "__pycache__" and item.is_dir():
            continue
        if item.is_file():
            target = dest / item.name
            try:
                target.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            except UnicodeDecodeError:
                target.write_bytes(item.read_bytes())
        elif item.is_dir():
            subdir = dest / item.name
            subdir.mkdir(parents=True, exist_ok=True)
            _copy_tree(item, subdir)


def build(
    format_id: str,
    rules: list[Rule],
    output_dir: Path,
    repo_name: str,
) -> BuildResult:
    """Assemble a poisoned context file and project skeleton.

    Steps:
    1. Load the base template for the format
    2. Resolve each rule's content for the format's syntax type
    3. Insert rules at section markers
    4. Strip all section markers
    5. Write the assembled context file to the repo directory
    6. Copy the project skeleton
    7. Generate the prompt reference
    8. Write the manifest

    Args:
        format_id: Target format (e.g., "cursorrules", "claude-md").
        rules: Selected rules to insert.
        output_dir: Parent directory for the generated repo.
        repo_name: Directory name for the generated repo.

    Returns:
        BuildResult with paths and metadata.

    Raises:
        ValueError: If format_id is not recognized.
    """
    fmt = get_format(format_id)
    if fmt is None:
        raise ValueError(f"Unknown format: {format_id!r}")

    # 1. Load base template
    base_content = load_base(format_id)

    # 2-3. Insert rules at section markers
    assembled = insert_rules(base_content, rules, fmt.syntax)

    # 4. Strip all section markers
    clean_content = strip_markers(assembled)

    # 5. Create repo dir and write assembled context file
    repo_dir = output_dir / repo_name
    repo_dir.mkdir(parents=True, exist_ok=True)

    context_file = repo_dir / fmt.filename
    context_file.parent.mkdir(parents=True, exist_ok=True)
    context_file.write_text(clean_content, encoding="utf-8")

    # 6. Copy project skeleton
    skeleton = resource_files("q_ai.cxp.techniques") / "skeleton"
    _copy_tree(skeleton, repo_dir)

    # 7. Generate prompt reference
    prompt_ref_content = generate_prompt_reference(rules)
    prompt_ref_path = repo_dir / "prompt-reference.md"
    prompt_ref_path.write_text(prompt_ref_content, encoding="utf-8")

    # 8. Write manifest
    manifest = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "format_id": format_id,
        "repo_name": repo_name,
        "rules_inserted": [r.id for r in rules],
        "prompt_reference": "prompt-reference.md",
    }
    manifest_path = repo_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return BuildResult(
        repo_dir=repo_dir,
        context_file=context_file,
        rules_inserted=[r.id for r in rules],
        format_id=format_id,
        prompt_reference_path=prompt_ref_path,
        manifest_path=manifest_path,
    )
