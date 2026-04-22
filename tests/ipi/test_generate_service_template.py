"""Tests for template_id stamping in generate_service.generate_documents."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from q_ai.ipi import generate_service
from q_ai.ipi.generate_service import generate_documents
from q_ai.ipi.models import (
    Campaign,
    CitationFrame,
    DocumentTemplate,
    Format,
    PayloadStyle,
    PayloadType,
    Technique,
)


def _stub_campaign(filename: str, technique: str) -> Campaign:
    """Build a bare Campaign matching the shape real generators produce."""
    return Campaign(
        id=uuid.uuid4().hex,
        uuid=uuid.uuid4().hex,
        token=uuid.uuid4().hex,
        filename=filename,
        format="markdown",
        technique=technique,
        callback_url="http://localhost:8080/c/stub",
    )


def _stub_single(file_path: Path, tech: Technique, *_: Any, **__: Any) -> Campaign:
    """Stub single-technique generator that does not write to disk."""
    return _stub_campaign(filename=file_path.name, technique=tech.value)


def _stub_batch(
    output_dir: Path,
    callback_url: str,
    base_name: str,
    payload_style: PayloadStyle,
    payload_type: PayloadType,
    techniques: Iterable[Technique],
    **_: Any,
) -> list[Campaign]:
    """Stub batch generator that emits one Campaign per technique."""
    del callback_url, payload_style, payload_type, output_dir, base_name
    return [_stub_campaign(filename=f"report_{t.value}.md", technique=t.value) for t in techniques]


@pytest.fixture
def stub_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace the Format.MARKDOWN dispatch entry with in-memory stubs."""
    patched = dict(generate_service._FORMAT_DISPATCH)
    patched[Format.MARKDOWN] = (_stub_single, _stub_batch)
    monkeypatch.setattr(generate_service, "_FORMAT_DISPATCH", patched)


@pytest.fixture
def _no_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace _save_campaign with a no-op so tests stay DB-free."""
    monkeypatch.setattr(generate_service, "_save_campaign", lambda campaign, seed: None)


class TestTemplateIdStamping:
    """generate_documents stamps template.value on every Campaign it returns."""

    @pytest.mark.usefixtures("stub_dispatch", "_no_persist")
    def test_single_technique_stamps_explicit_template(self, tmp_path: Path) -> None:
        """A non-default template value flows to Campaign.template_id in the single branch."""
        result = generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT],
            template=DocumentTemplate.WHOIS,
        )

        assert len(result.campaigns) == 1
        assert result.campaigns[0].template_id == "whois"

    @pytest.mark.usefixtures("stub_dispatch", "_no_persist")
    def test_single_technique_defaults_to_generic(self, tmp_path: Path) -> None:
        """Omitting template defaults to GENERIC and stamps 'generic'."""
        result = generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT],
        )

        assert len(result.campaigns) == 1
        assert result.campaigns[0].template_id == "generic"

    @pytest.mark.usefixtures("stub_dispatch", "_no_persist")
    def test_batch_stamps_every_campaign(self, tmp_path: Path) -> None:
        """Batch generation stamps template_id on every generated Campaign."""
        result = generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT, Technique.ZERO_WIDTH],
            template=DocumentTemplate.WHOIS,
        )

        assert len(result.campaigns) == 2
        assert all(c.template_id == "whois" for c in result.campaigns)

    @pytest.mark.usefixtures("stub_dispatch", "_no_persist")
    def test_batch_defaults_to_generic(self, tmp_path: Path) -> None:
        """Batch generation without an explicit template stamps 'generic' everywhere."""
        result = generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT, Technique.ZERO_WIDTH],
        )

        assert len(result.campaigns) == 2
        assert all(c.template_id == "generic" for c in result.campaigns)

    @pytest.mark.usefixtures("stub_dispatch")
    def test_persisted_campaign_carries_template_id(self, tmp_path: Path) -> None:
        """The Campaign handed to db.save_campaign already has template_id stamped."""
        seen: list[str | None] = []

        def fake_save(campaign: Campaign) -> None:
            seen.append(campaign.template_id)

        with patch.object(generate_service.db, "save_campaign", side_effect=fake_save):
            generate_documents(
                callback_url="http://localhost:8080",
                output=tmp_path,
                format_name=Format.MARKDOWN,
                techniques=[Technique.HTML_COMMENT],
                template=DocumentTemplate.WHOIS,
            )

        assert seen == ["whois"]


class TestCitationFrameForwarding:
    """generate_documents forwards citation_frame to single- and batch-path generators."""

    @pytest.mark.usefixtures("_no_persist")
    def test_single_technique_forwards_citation_frame(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Single-path single_fn receives the explicit citation_frame kwarg."""
        seen: dict[str, Any] = {}

        def capture_single(
            file_path: Path,
            tech: Technique,
            *args: Any,
            **kwargs: Any,
        ) -> Campaign:
            del args
            seen.update(kwargs)
            return _stub_campaign(filename=file_path.name, technique=tech.value)

        patched = dict(generate_service._FORMAT_DISPATCH)
        patched[Format.MARKDOWN] = (capture_single, _stub_batch)
        monkeypatch.setattr(generate_service, "_FORMAT_DISPATCH", patched)

        generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT],
            payload_style=PayloadStyle.CITATION,
            payload_type=PayloadType.CALLBACK,
            citation_frame=CitationFrame.PLAIN,
        )

        assert seen["citation_frame"] == CitationFrame.PLAIN

    @pytest.mark.usefixtures("_no_persist")
    def test_batch_forwards_citation_frame(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Batch-path batch_fn receives the explicit citation_frame kwarg."""
        seen: dict[str, Any] = {}

        def capture_batch(
            output_dir: Path,
            callback_url: str,
            base_name: str,
            payload_style: PayloadStyle,
            payload_type: PayloadType,
            techniques: Iterable[Technique],
            **kwargs: Any,
        ) -> list[Campaign]:
            del callback_url, payload_style, payload_type, output_dir, base_name
            seen.update(kwargs)
            return [
                _stub_campaign(filename=f"report_{t.value}.md", technique=t.value)
                for t in techniques
            ]

        patched = dict(generate_service._FORMAT_DISPATCH)
        patched[Format.MARKDOWN] = (_stub_single, capture_batch)
        monkeypatch.setattr(generate_service, "_FORMAT_DISPATCH", patched)

        generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT, Technique.ZERO_WIDTH],
            payload_style=PayloadStyle.CITATION,
            payload_type=PayloadType.CALLBACK,
            citation_frame=CitationFrame.PLAIN,
        )

        assert seen["citation_frame"] == CitationFrame.PLAIN

    @pytest.mark.usefixtures("stub_dispatch", "_no_persist")
    def test_default_is_template_aware(self, tmp_path: Path) -> None:
        """Omitting citation_frame preserves the TEMPLATE_AWARE default."""
        # Stubs don't capture kwargs, but the call must succeed — a missing
        # default would break parameter propagation before reaching the stub.
        result = generate_documents(
            callback_url="http://localhost:8080",
            output=tmp_path,
            format_name=Format.MARKDOWN,
            techniques=[Technique.HTML_COMMENT],
        )
        assert len(result.campaigns) == 1
