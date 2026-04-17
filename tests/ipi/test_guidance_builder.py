"""Tests for q_ai.ipi.guidance_builder.build_ipi_guidance."""

from __future__ import annotations

from q_ai.core.guidance import BlockKind, RunGuidance
from q_ai.ipi.generate_service import GenerateResult
from q_ai.ipi.guidance_builder import build_ipi_guidance
from q_ai.ipi.models import Campaign, Format


def _make_campaign(**overrides: object) -> Campaign:
    """Create a Campaign with sensible defaults, applying any overrides.

    Args:
        **overrides: Keyword arguments forwarded to Campaign().

    Returns:
        A Campaign instance with all required fields populated.
    """
    defaults: dict[str, object] = {
        "id": "camp-1",
        "uuid": "uuid-1",
        "token": "token-1",
        "filename": "report_white_ink.pdf",
        "format": "pdf",
        "technique": "white_ink",
        "callback_url": "http://localhost:8080/c/uuid-1/token-1",
        "payload_style": "obvious",
        "payload_type": "callback",
    }
    defaults.update(overrides)
    return Campaign(**defaults)  # type: ignore[arg-type]


def _build_default_guidance(
    campaigns: list[Campaign] | None = None,
    format_name: Format = Format.PDF,
) -> RunGuidance:
    """Build guidance with a single default campaign unless overridden.

    Args:
        campaigns: Optional campaign list; defaults to one campaign.
        format_name: Document format passed to the builder.

    Returns:
        RunGuidance produced by the builder.
    """
    if campaigns is None:
        campaigns = [_make_campaign()]
    result = GenerateResult(campaigns=campaigns, skipped=0, errors=[])
    return build_ipi_guidance(
        result=result,
        format_name=format_name,
        callback_url="http://localhost:8080",
        payload_style="obvious",
        payload_type="callback",
    )


class TestBuildIPIGuidance:
    """Tests for build_ipi_guidance output structure and content."""

    def test_returns_run_guidance(self) -> None:
        """Basic structure: returns RunGuidance, module is ipi, has 4 blocks."""
        guidance = _build_default_guidance()

        assert isinstance(guidance, RunGuidance)
        assert guidance.module == "ipi"
        assert len(guidance.blocks) == 4

    def test_inventory_block_structure(self) -> None:
        """Inventory block has correct kind, metadata rows, and row keys."""
        campaigns = [
            _make_campaign(id="c1", uuid="u1", token="t1", filename="a.pdf"),
            _make_campaign(id="c2", uuid="u2", token="t2", filename="b.pdf"),
        ]
        guidance = _build_default_guidance(campaigns=campaigns)
        inventory = guidance.blocks[0]

        assert inventory.kind == BlockKind.INVENTORY
        assert "rows" in inventory.metadata

        rows = inventory.metadata["rows"]
        assert len(rows) == len(campaigns)

        expected_keys = {"filename", "technique", "callback_url", "token", "template_id"}
        for row in rows:
            assert expected_keys <= set(row.keys())

    def test_inventory_rows_carry_template_id(self) -> None:
        """Each inventory row dict includes the source Campaign's template_id."""
        campaigns = [
            _make_campaign(id="c1", uuid="u1", token="t1", filename="a.pdf", template_id="whois"),
            _make_campaign(id="c2", uuid="u2", token="t2", filename="b.pdf", template_id=None),
        ]
        guidance = _build_default_guidance(campaigns=campaigns)
        rows = guidance.blocks[0].metadata["rows"]

        assert [row["template_id"] for row in rows] == ["whois", None]

    def test_inventory_items_string_includes_template(self) -> None:
        """Human-readable items strings surface template_id so text-only
        renderings match the metadata row dicts and the UI's Template
        column. NULL template_id uses the same em-dash fallback as the
        inventory table (ipi_tab.html)."""
        campaigns = [
            _make_campaign(id="c1", uuid="u1", token="t1", filename="a.pdf", template_id="whois"),
            _make_campaign(id="c2", uuid="u2", token="t2", filename="b.pdf", template_id=None),
        ]
        guidance = _build_default_guidance(campaigns=campaigns)
        items = guidance.blocks[0].items

        assert len(items) == 2
        assert "template: whois" in items[0]
        # NULL template_id renders as an em-dash, matching the UI fallback.
        assert "template: \u2014" in items[1]

    def test_trigger_prompts_block_has_all_profiles(self) -> None:
        """Trigger prompts block has anythingllm, open_webui, and generic keys."""
        guidance = _build_default_guidance()
        trigger = guidance.blocks[1]

        assert trigger.kind == BlockKind.TRIGGER_PROMPTS
        assert "anythingllm" in trigger.metadata
        assert "open_webui" in trigger.metadata
        assert "generic" in trigger.metadata

    def test_trigger_prompts_format_aware(self) -> None:
        """Trigger prompt text differs between PDF and MARKDOWN formats."""
        pdf_guidance = _build_default_guidance(format_name=Format.PDF)
        md_guidance = _build_default_guidance(format_name=Format.MARKDOWN)

        pdf_prompts = pdf_guidance.blocks[1].metadata
        md_prompts = md_guidance.blocks[1].metadata

        assert pdf_prompts["anythingllm"] != md_prompts["anythingllm"]
        assert pdf_prompts["open_webui"] != md_prompts["open_webui"]
        assert pdf_prompts["generic"] != md_prompts["generic"]

    def test_trigger_prompts_all_formats_covered(self) -> None:
        """Every Format enum value produces trigger prompts with all profiles."""
        expected_profiles = {"anythingllm", "open_webui", "generic"}

        for fmt in Format:
            guidance = _build_default_guidance(format_name=fmt)
            trigger = guidance.blocks[1]

            assert expected_profiles <= set(trigger.metadata.keys()), (
                f"Format {fmt.value} missing profiles"
            )
            for profile in expected_profiles:
                value = trigger.metadata[profile]
                assert isinstance(value, str) and len(value) > 0, (
                    f"Format {fmt.value}, profile {profile} is empty"
                )

    def test_deployment_steps_block(self) -> None:
        """Deployment steps block is correct kind with format name in first item."""
        guidance = _build_default_guidance(format_name=Format.PDF)
        deployment = guidance.blocks[2]

        assert deployment.kind == BlockKind.DEPLOYMENT_STEPS
        assert len(deployment.items) > 0
        assert "PDF" in deployment.items[0]

    def test_monitoring_block(self) -> None:
        """Monitoring block mentions HIGH, MEDIUM, and LOW confidence levels."""
        guidance = _build_default_guidance()
        monitoring = guidance.blocks[3]

        assert monitoring.kind == BlockKind.MONITORING
        combined = " ".join(monitoring.items)
        assert "HIGH" in combined
        assert "MEDIUM" in combined
        assert "LOW" in combined

    def test_empty_campaigns(self) -> None:
        """Empty campaign list produces empty inventory rows and items."""
        guidance = _build_default_guidance(campaigns=[])
        inventory = guidance.blocks[0]

        assert inventory.metadata["rows"] == []
        assert inventory.items == []
