"""Render tests for the IPI tab hit-feed partial.

Covers the ``partials/ipi_tab.html`` source-IP cell rendering the
tunnel-source qualifier badge:

- Truthy ``h.via_tunnel`` → ``<span class="badge badge-xs badge-outline">tunnel</span>``.
- Falsy ``h.via_tunnel`` → ``<span class="badge badge-xs badge-ghost">direct</span>``.

Rendering is scoped to a single ``<td>`` (the source-IP cell of the row
under test) to avoid coupling to unrelated token/confidence badges in
the same row or to other rows.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "q_ai" / "server" / "templates"

# The Template column is 4th in the row (Time, UUID, Confidence, IP, Token),
# but we're asserting on the source-IP cell specifically — that's the 4th
# <td> in each tbody row.
_SOURCE_IP_COL_IDX = 3

_TR_BODY_RE = re.compile(r"<tr[^>]*data-hit-id=\"([^\"]+)\"[^>]*>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)


def _render_ipi_tab(hits: list[dict[str, Any]]) -> str:
    """Render ``partials/ipi_tab.html`` with the given hits list.

    The hit-feed section is nested inside the playbook-mode branch
    (``{% if ipi_guidance %}``). A minimal truthy ``ipi_guidance`` with
    an empty ``blocks`` list satisfies that gate without invoking the
    ``render_guidance_block`` macro — which would require real block
    data. This isolates the render test to the hit-feed row loop.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("partials/ipi_tab.html")
    return template.render(
        ipi_hits=hits,
        ipi_guidance=SimpleNamespace(blocks=[]),
    )


def _source_ip_td_for_hit(html: str, hit_id: str) -> str:
    """Return the inner HTML of the source-IP ``<td>`` for a given hit row."""
    for found_id, body in _TR_BODY_RE.findall(html):
        if found_id != hit_id:
            continue
        cells = _TD_RE.findall(body)
        if len(cells) <= _SOURCE_IP_COL_IDX:
            raise AssertionError(
                f"row for hit {hit_id!r} has {len(cells)} <td>s; "
                f"expected at least {_SOURCE_IP_COL_IDX + 1}"
            )
        return cells[_SOURCE_IP_COL_IDX]
    raise AssertionError(f"no <tr data-hit-id={hit_id!r}> found in rendered HTML")


def _hit_dict(
    *,
    hit_id: str,
    uuid_val: str = "payload-uuid",
    source_ip: str = "203.0.113.7",
    confidence: str = "high",
    token_valid: int = 1,
    via_tunnel: int = 0,
    timestamp: str = "2026-04-17T12:00:00+00:00",
) -> dict[str, Any]:
    """Shape matching what ``run_service._load_run_results`` passes into
    the ``ipi_hits`` template context — sqlite3.Row coerced to dict."""
    return {
        "id": hit_id,
        "uuid": uuid_val,
        "source_ip": source_ip,
        "confidence": confidence,
        "token_valid": token_valid,
        "via_tunnel": via_tunnel,
        "timestamp": timestamp,
    }


class TestTunnelSourceBadge:
    """The source-IP cell carries a tunnel/direct qualifier badge
    that mirrors the ``h.via_tunnel`` flag."""

    def test_tunnel_hit_renders_outline_badge(self) -> None:
        hits = [_hit_dict(hit_id="hit-tunnel", source_ip="203.0.113.7", via_tunnel=1)]
        html = _render_ipi_tab(hits)

        cell = _source_ip_td_for_hit(html, "hit-tunnel")
        assert "203.0.113.7" in cell
        # The outline variant signals the noteworthy tunnel case.
        assert "badge-outline" in cell
        assert ">tunnel<" in cell
        # Direct fallback must NOT appear on a tunnel row.
        assert ">direct<" not in cell
        assert "badge-ghost" not in cell

    def test_direct_hit_renders_ghost_badge(self) -> None:
        hits = [_hit_dict(hit_id="hit-direct", source_ip="10.0.0.1", via_tunnel=0)]
        html = _render_ipi_tab(hits)

        cell = _source_ip_td_for_hit(html, "hit-direct")
        assert "10.0.0.1" in cell
        # Ghost variant = muted default/expected state.
        assert "badge-ghost" in cell
        assert ">direct<" in cell
        # Tunnel fallback must NOT appear on a direct row.
        assert ">tunnel<" not in cell
        assert "badge-outline" not in cell

    def test_mixed_rows_carry_correct_per_row_badge(self) -> None:
        """Each row's badge tracks its own flag — not the first row's."""
        hits = [
            _hit_dict(hit_id="h-tun", via_tunnel=1),
            _hit_dict(hit_id="h-dir", via_tunnel=0),
        ]
        html = _render_ipi_tab(hits)

        tun_cell = _source_ip_td_for_hit(html, "h-tun")
        dir_cell = _source_ip_td_for_hit(html, "h-dir")

        assert ">tunnel<" in tun_cell
        assert ">direct<" in dir_cell
