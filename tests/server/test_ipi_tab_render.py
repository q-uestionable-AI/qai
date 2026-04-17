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

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "q_ai" / "server" / "templates"

# Columns in each tbody row, in declared order: Timestamp, Campaign (UUID),
# Confidence, Source IP, Token. The Source-IP cell is the 4th <td>
# (0-based index 3). Kept as a module constant so a future column reshuffle
# updates here once rather than in every test.
_SOURCE_IP_COL_IDX = 3


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


class _SourceIpCellExtractor(HTMLParser):
    """Pull the inner HTML of the source-IP ``<td>`` for a specific row.

    Walking the rendered HTML with :class:`html.parser.HTMLParser` keeps
    the assertion stable against benign template reflows — attribute
    reordering, single- vs double-quoted values, or whitespace tweaks
    that would silently break a regex-based scan. The parser tracks
    entry into the target ``<tr>`` (matched by ``data-hit-id``), counts
    top-level ``<td>``s within that row, and once it hits the
    ``_SOURCE_IP_COL_IDX``-th one, reserializes everything between the
    opening and matching closing ``<td>`` into an inner-HTML string.
    Reserialization preserves the substrings the tests assert on —
    class tokens, data text, and the ``">tunnel<"``/`">direct<"`
    marker between the badge span's tags.
    """

    def __init__(self, target_hit_id: str, cell_index: int) -> None:
        super().__init__(convert_charrefs=False)
        self._target = target_hit_id
        self._cell_index = cell_index
        self._in_target_row = False
        self._td_counter = 0
        self._in_target_cell = False
        self._nest_depth = 0
        self._captured_parts: list[str] = []
        self.result: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_target_row = dict(attrs).get("data-hit-id") == self._target
            if self._in_target_row:
                self._td_counter = 0
            return
        if not self._in_target_row:
            return
        if self._in_target_cell:
            self._captured_parts.append(self._format_starttag(tag, attrs))
            self._nest_depth += 1
            return
        if tag == "td":
            if self._td_counter == self._cell_index:
                self._in_target_cell = True
                self._nest_depth = 0
                self._captured_parts = []
            self._td_counter += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._in_target_row:
            return
        if self._in_target_cell:
            if tag == "td" and self._nest_depth == 0:
                self.result = "".join(self._captured_parts)
                self._in_target_cell = False
                return
            self._captured_parts.append(f"</{tag}>")
            self._nest_depth -= 1
            return
        if tag == "tr":
            self._in_target_row = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing shape (``<br/>`` etc.) — unlikely inside a table cell
        # but handled for completeness so a template tweak can't silently
        # drop content from the captured snapshot.
        if self._in_target_cell:
            self._captured_parts.append(self._format_starttag(tag, attrs))

    def handle_data(self, data: str) -> None:
        if self._in_target_cell:
            self._captured_parts.append(data)

    @staticmethod
    def _format_starttag(tag: str, attrs: list[tuple[str, str | None]]) -> str:
        """Reserialize a start tag in a stable shape for substring asserts."""
        parts = [tag]
        for name, value in attrs:
            if value is None:
                parts.append(name)
            else:
                parts.append(f'{name}="{value}"')
        return "<" + " ".join(parts) + ">"


def _source_ip_td_for_hit(html: str, hit_id: str) -> str:
    """Return the inner HTML of the source-IP ``<td>`` for a given hit row."""
    extractor = _SourceIpCellExtractor(hit_id, _SOURCE_IP_COL_IDX)
    extractor.feed(html)
    extractor.close()
    if extractor.result is None:
        raise AssertionError(f"no source-IP <td> found for row with data-hit-id={hit_id!r}")
    return extractor.result


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
    """Shape matching what the template actually receives at render time.

    Both render paths into ``partials/ipi_tab.html`` — the server-render
    via ``run_service._load_run_results`` and the WebSocket broadcast via
    ``internal._read_hit`` — expose the ``ipi_hits.via_tunnel`` column as
    a raw ``dict(sqlite3.Row)`` value. SQLite INTEGER columns surface as
    Python ``int`` through that conversion, NOT ``bool`` — the bool shape
    only appears after ``Hit.from_row`` hydration, which isn't on this
    render path. Modeling ``via_tunnel`` as ``int`` here matches the
    production shape exactly. Jinja truthiness treats 0/1 and False/True
    identically, so the template works either way; the int model is what
    catches a regression in the data-shape contract between DB and
    renderer.
    """
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
