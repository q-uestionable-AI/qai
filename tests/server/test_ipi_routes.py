"""Tests for the IPI inventory route and tab partial rendering.

Covers:
- ``_load_campaigns`` surfaces ``template_id`` on each row.
- ``GET /api/ipi/campaigns`` renders the Template column with a badge when
  populated and an em-dash fallback when NULL.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from q_ai.ipi import db as ipi_db
from q_ai.ipi.models import Campaign
from q_ai.server.routes.modules.ipi import _load_campaigns

# Column ordinal for the Template <td> inside each tbody row. The partial
# renders columns in declared order: UUID, Format, Technique, Template,
# Hits, Created. Keeping this as a constant lets the column reshuffle
# here in one place if the table layout ever changes.
_TEMPLATE_COL_IDX = 3

_TR_BODY_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_BADGE_GHOST_TEXT_RE = re.compile(
    r'<span[^>]*class="[^"]*\bbadge-ghost\b[^"]*"[^>]*>\s*([^<\s]+)\s*</span>',
    re.DOTALL,
)


def _template_td_for_uuid(html: str, uuid_hex: str) -> str:
    """Return the inner HTML of the Template ``<td>`` for a specific row.

    The inventory table's UUID cell renders ``{{ c.uuid[:8] }}...`` inside
    a ``<span>``; locate the row whose body contains that 8-char prefix
    and return the ``<td>`` at :data:`_TEMPLATE_COL_IDX`. Scoping
    assertions to a single cell prevents false positives or negatives
    when other rows or unrelated table cells happen to contain the
    tokens being tested for.

    Args:
        html: Full rendered response body.
        uuid_hex: Campaign UUID (full hex); only the first 8 chars are
            used to match the row since the partial truncates.

    Returns:
        Inner HTML of the Template ``<td>`` for that row.

    Raises:
        AssertionError: No row containing the 8-char prefix was found, or
            the row has fewer ``<td>``s than :data:`_TEMPLATE_COL_IDX`.
    """
    prefix = uuid_hex[:8]
    for body in _TR_BODY_RE.findall(html):
        if prefix not in body:
            continue
        cells = _TD_RE.findall(body)
        if len(cells) <= _TEMPLATE_COL_IDX:
            raise AssertionError(
                f"row for uuid prefix {prefix!r} has {len(cells)} <td>s; "
                f"expected at least {_TEMPLATE_COL_IDX + 1}"
            )
        return cells[_TEMPLATE_COL_IDX]
    raise AssertionError(f"no row found containing uuid prefix {prefix!r}")


def _make_campaign(
    *,
    template_id: str | None,
    doc_format: str = "pdf",
    technique: str = "white_ink",
) -> Campaign:
    """Build a Campaign for route/partial tests."""
    return Campaign(
        id=uuid.uuid4().hex,
        uuid=uuid.uuid4().hex,
        token=uuid.uuid4().hex,
        filename=f"payload_{technique}.{doc_format}",
        format=doc_format,
        technique=technique,
        callback_url="http://localhost:8080/c/x",
        template_id=template_id,
        created_at=datetime.now(UTC),
    )


def _legacy_insert(db_path: Path, campaign: Campaign) -> None:
    """Insert a campaign row with template_id explicitly NULL.

    Mirrors how a legacy (pre-v13) row would look after an ALTER TABLE — the
    column exists but was never populated.
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO ipi_payloads (
                id, uuid, token, filename, output_path,
                format, technique, payload_style, payload_type,
                callback_url, template_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign.id,
                campaign.uuid,
                campaign.token,
                campaign.filename,
                None,
                campaign.format,
                campaign.technique,
                campaign.payload_style,
                campaign.payload_type,
                campaign.callback_url,
                None,
                campaign.created_at.isoformat(),
            ),
        )
        conn.commit()


class TestLoadCampaignsTemplateId:
    """_load_campaigns surfaces template_id for the inventory partial."""

    def test_populated_template_id_flows_through(self, tmp_db: Path) -> None:
        """A saved campaign with template_id surfaces that value in the row dict."""
        campaign = _make_campaign(template_id="whois")
        ipi_db.save_campaign(campaign, db_path=tmp_db)

        data = _load_campaigns(tmp_db)
        assert len(data["campaigns"]) == 1
        row = data["campaigns"][0]
        assert row["uuid"] == campaign.uuid
        assert row["template_id"] == "whois"

    def test_null_template_id_surfaces_as_none(self, tmp_db: Path) -> None:
        """A legacy row with NULL template_id surfaces as None in the row dict."""
        campaign = _make_campaign(template_id=None)
        _legacy_insert(tmp_db, campaign)

        data = _load_campaigns(tmp_db)
        assert len(data["campaigns"]) == 1
        assert data["campaigns"][0]["template_id"] is None


class TestIpiTabTemplateColumn:
    """GET /api/ipi/campaigns renders the Template column correctly."""

    def test_header_includes_template_column(self, client: TestClient, tmp_db: Path) -> None:
        """The rendered table header lists Template between Technique and Hits."""
        ipi_db.save_campaign(_make_campaign(template_id="generic"), db_path=tmp_db)

        resp = client.get("/api/ipi/campaigns")
        assert resp.status_code == 200
        html = resp.text
        # Headers appear in declared order; confirm the sequence.
        technique_idx = html.index("<th>Technique</th>")
        template_idx = html.index("<th>Template</th>")
        hits_idx = html.index("<th>Hits</th>")
        assert technique_idx < template_idx < hits_idx

    def test_populated_template_renders_badge(self, client: TestClient, tmp_db: Path) -> None:
        """A populated template_id renders inside a ghost badge with the alias text."""
        campaign = _make_campaign(template_id="whois")
        ipi_db.save_campaign(campaign, db_path=tmp_db)

        resp = client.get("/api/ipi/campaigns")
        assert resp.status_code == 200

        cell = _template_td_for_uuid(resp.text, campaign.uuid)
        badge = _BADGE_GHOST_TEXT_RE.search(cell)
        assert badge is not None, (
            f"expected a <span class='... badge-ghost ...'> in the Template <td>, got: {cell!r}"
        )
        assert badge.group(1) == "whois"

    def test_null_template_renders_em_dash(self, client: TestClient, tmp_db: Path) -> None:
        """A legacy NULL template_id renders the em-dash fallback, not a ghost badge."""
        legacy = _make_campaign(template_id=None)
        _legacy_insert(tmp_db, legacy)

        resp = client.get("/api/ipi/campaigns")
        assert resp.status_code == 200

        cell = _template_td_for_uuid(resp.text, legacy.uuid)
        assert "&mdash;" in cell
        # The ghost badge is reserved for populated template_id; it must be
        # absent in the specific Template cell for this legacy NULL row.
        # (The partial itself may render badge-ghost on other columns of
        # other rows, so the check is scoped to this cell only.)
        assert "badge-ghost" not in cell

    def test_generic_template_renders_as_badge(self, client: TestClient, tmp_db: Path) -> None:
        """GENERIC is a real control condition, not a NULL — it renders a badge."""
        campaign = _make_campaign(template_id="generic")
        ipi_db.save_campaign(campaign, db_path=tmp_db)

        resp = client.get("/api/ipi/campaigns")
        assert resp.status_code == 200

        cell = _template_td_for_uuid(resp.text, campaign.uuid)
        badge = _BADGE_GHOST_TEXT_RE.search(cell)
        assert badge is not None, (
            f"expected a <span class='... badge-ghost ...'> in the Template <td>, got: {cell!r}"
        )
        assert badge.group(1) == "generic"
