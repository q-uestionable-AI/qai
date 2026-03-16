"""Domain profile loader for RXP retrieval testing."""

from __future__ import annotations

from pathlib import Path

import yaml

from q_ai.rxp.models import CorpusDocument, DomainProfile

_PROFILES_DIR = Path(__file__).parent


def _load_profile(profile_dir: Path) -> DomainProfile:
    """Load a single domain profile from its directory.

    Args:
        profile_dir: Path to the profile directory containing profile.yaml.

    Returns:
        Loaded DomainProfile instance.
    """
    yaml_path = profile_dir / "profile.yaml"
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return DomainProfile(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        corpus_dir=str(profile_dir),
        queries=data["queries"],
    )


def list_profiles() -> list[DomainProfile]:
    """Return all built-in domain profiles."""
    profiles: list[DomainProfile] = [
        _load_profile(child)
        for child in sorted(_PROFILES_DIR.iterdir())
        if child.is_dir() and (child / "profile.yaml").exists()
    ]
    return profiles


def get_profile(profile_id: str) -> DomainProfile | None:
    """Get a domain profile by ID."""
    for profile in list_profiles():
        if profile.id == profile_id:
            return profile
    return None


def load_corpus(profile: DomainProfile) -> list[CorpusDocument]:
    """Load corpus documents from a profile's corpus directory.

    Reads all .txt files from the profile's corpus directory.
    Each file becomes one CorpusDocument.

    Args:
        profile: The domain profile to load corpus from.

    Returns:
        List of CorpusDocument instances.
    """
    corpus_dir = Path(profile.corpus_dir) / "corpus"
    documents: list[CorpusDocument] = []
    if not corpus_dir.exists():
        return documents
    for txt_file in sorted(corpus_dir.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8").strip()
        documents.append(
            CorpusDocument(
                id=txt_file.stem,
                text=text,
                source=str(txt_file),
            )
        )
    return documents


def load_poison(profile: DomainProfile) -> list[CorpusDocument]:
    """Load poison documents from a profile's poison directory.

    Reads all .txt files from the profile's poison directory.
    Each file becomes one CorpusDocument with is_poison=True.

    Args:
        profile: The domain profile to load poison docs from.

    Returns:
        List of CorpusDocument instances with is_poison=True.
    """
    poison_dir = Path(profile.corpus_dir) / "poison"
    documents: list[CorpusDocument] = []
    if not poison_dir.exists():
        return documents
    for txt_file in sorted(poison_dir.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8").strip()
        documents.append(
            CorpusDocument(
                id=txt_file.stem,
                text=text,
                source=str(txt_file),
                is_poison=True,
            )
        )
    return documents
