"""Distribution identity tests."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRIMARY_PROJECT = _REPO_ROOT / "pyproject.toml"


def _load_document() -> dict[str, object]:
    with _PRIMARY_PROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_distribution_and_wheel_use_ctpf_identity() -> None:
    """The distribution, entry point, and wheel package are canonical."""
    document = _load_document()
    project = document.get("project")
    build = document.get("tool")
    assert isinstance(project, dict)
    assert isinstance(build, dict)

    assert project["name"] == "ctpf"
    assert project["scripts"] == {"ctpf": "ctpf.cli:app"}
    assert build["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/ctpf"]


def test_legacy_import_namespace_is_absent() -> None:
    """The retired q_ai import namespace has no package or shim."""
    assert importlib.util.find_spec("q_ai") is None
