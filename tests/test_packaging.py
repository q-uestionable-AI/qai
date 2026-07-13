"""Distribution metadata compatibility tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRIMARY_PROJECT = _REPO_ROOT / "pyproject.toml"
_COMPAT_PROJECT = _REPO_ROOT / "compat" / "q-uestionable-ai" / "pyproject.toml"


def _load_project(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    project = document.get("project")
    assert isinstance(project, dict)
    return project


def test_primary_distribution_is_ctpf() -> None:
    """The application wheel uses the preferred PyPI distribution name."""
    project = _load_project(_PRIMARY_PROJECT)
    assert project["name"] == "ctpf"


def test_compatibility_distribution_tracks_primary_version() -> None:
    """The legacy installer delegates to the exact same primary version."""
    primary = _load_project(_PRIMARY_PROJECT)
    compatibility = _load_project(_COMPAT_PROJECT)
    version = primary["version"]

    assert compatibility["name"] == "q-uestionable-ai"
    assert compatibility["version"] == version
    assert compatibility["dependencies"] == [f"ctpf=={version}"]


def test_compatibility_project_contains_no_import_package() -> None:
    """The legacy distribution remains a metadata-only compatibility bridge."""
    compatibility_root = _COMPAT_PROJECT.parent
    files = {
        path.relative_to(compatibility_root)
        for path in compatibility_root.rglob("*")
        if path.is_file()
    }
    assert files == {Path("README.md"), Path("pyproject.toml")}
