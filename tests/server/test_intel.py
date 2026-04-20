"""Tests for the Intel page routes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from q_ai.core.db import create_evidence, create_run, create_target, update_run_status
from q_ai.core.models import RunStatus


def _open_db(path: Path) -> sqlite3.Connection:
    """Open a sqlite connection against the test DB with FK + row factory."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class TestIntelPage:
    """GET /intel renders the Intel page."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert resp.status_code == 200

    def test_contains_nav_link(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert 'href="/intel"' in resp.text

    def test_nav_link_present_on_other_pages(self, client: TestClient) -> None:
        resp = client.get("/launcher")
        assert 'href="/intel"' in resp.text

    def test_contains_import_card(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert "Import Results" in resp.text
        assert "form-import" in resp.text

    def test_contains_probe_card(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert "Probe Model" in resp.text
        assert "form-probe" in resp.text

    def test_contains_sweep_card(self, client: TestClient) -> None:
        """Sweep launcher card renders on /intel."""
        resp = client.get("/intel")
        assert 'id="card-sweep"' in resp.text
        assert "form-sweep" in resp.text
        assert "Launch Sweep" in resp.text

    def test_sweep_card_defaults(self, client: TestClient) -> None:
        """All 12 DocumentTemplate values and all 7 PayloadStyle values are preselected."""
        from q_ai.ipi.models import DocumentTemplate, PayloadStyle

        resp = client.get("/intel")
        text = resp.text
        for t in DocumentTemplate:
            assert f'value="{t.value}" selected' in text
        for s in PayloadStyle:
            assert f'value="{s.value}" selected' in text
        assert 'value="3"' in text  # reps default
        assert 'value="callback"' in text  # payload_type hidden input

    def test_format_options_present(self, client: TestClient) -> None:
        resp = client.get("/intel")
        for fmt in ["garak", "pyrit", "sarif", "scored", "bipia"]:
            assert fmt in resp.text.lower()

    def test_target_selector_populated(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            create_target(conn, type="server", name="intel-test-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/intel")
        assert "intel-test-target" in resp.text

    def test_target_list_shows_em_dash_when_no_evidence(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        """A target with no runs renders with em-dash age cells."""
        conn = _open_db(tmp_db)
        try:
            create_target(conn, type="server", name="no-evidence-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/intel")
        assert resp.status_code == 200
        assert "no-evidence-target" in resp.text
        # Three em-dash cells appear in the row (plus the URI em-dash → 4 total).
        assert resp.text.count("\u2014") >= 3

    def test_target_row_links_to_detail(self, tmp_db: Path, client: TestClient) -> None:
        """Target-list rows link to /intel/targets/<id>."""
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="clickable-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get("/intel")
        assert f'href="/intel/targets/{target_id}"' in resp.text

    def test_launcher_cards_render_with_only_synthetic_target(self, client: TestClient) -> None:
        """Fresh DB — Phase 5 migration creates the synthetic Unbound target on
        startup, so the target list is never empty. The launcher cards still
        render regardless.
        """
        resp = client.get("/intel")
        assert resp.status_code == 200
        assert "(Unbound historical intel)" in resp.text
        assert "form-import" in resp.text
        assert "form-probe" in resp.text


class TestIntelTargetDetail:
    """GET /intel/targets/<target_id> renders the detail page."""

    def test_valid_target_returns_200(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="detail-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        assert resp.status_code == 200
        assert "detail-target" in resp.text

    def test_all_three_section_headers_present(self, tmp_db: Path, client: TestClient) -> None:
        """Detail page renders the Imports / Probe Runs / Sweep Runs sections."""
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="sections-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        assert resp.status_code == 200
        text = resp.text
        assert 'id="imports"' in text
        assert 'id="probe-runs"' in text
        assert 'id="sweep-runs"' in text
        assert "Imports" in text
        assert "Probe Runs" in text
        assert "Sweep Runs" in text

    def test_empty_state_text_per_section(self, tmp_db: Path, client: TestClient) -> None:
        """Each section renders its distinct empty-state copy."""
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="empty-sections-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert "No imports yet." in text
        assert "No probe runs yet." in text
        assert "No sweep runs yet." in text
        assert "measure per-template compliance" in text
        assert "/intel#card-sweep" in text
        assert "measure IPI susceptibility" in text
        assert "/intel#card-probe" in text

    def test_nav_marks_intel_active(self, tmp_db: Path, client: TestClient) -> None:
        """Top nav highlights Intel on the detail page too."""
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="nav-target")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        # The base.html nav gives the active link an 'active bg-primary/10' class.
        assert 'href="/intel"' in resp.text
        assert "active bg-primary/10 text-primary" in resp.text

    def test_invalid_target_returns_404(self, client: TestClient) -> None:
        resp = client.get("/intel/targets/nonexistent-id")
        assert resp.status_code == 404

    def test_completed_runs_do_not_crash_detail(self, tmp_db: Path, client: TestClient) -> None:
        """Detail page renders cleanly when completed runs exist for the target."""
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="has-runs-target")
            run_id = create_run(conn, module="ipi-probe", target_id=target_id)
            update_run_status(conn, run_id, RunStatus.COMPLETED)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        assert resp.status_code == 200
        assert "has-runs-target" in resp.text


def _seed_sweep_run(
    conn: sqlite3.Connection,
    target_id: str,
    *,
    finished_at: str,
    template_count: int = 3,
    style_count: int = 2,
    total_cases: int = 12,
) -> str:
    """Create a completed ipi-sweep run with a matching metadata blob."""
    run_id = create_run(conn, module="ipi-sweep", target_id=target_id)
    update_run_status(conn, run_id, RunStatus.COMPLETED, finished_at=finished_at)
    blob = {
        "total_cases": total_cases,
        "total_complied": 0,
        "overall_compliance_rate": 0.0,
        "overall_severity": "INFO",
        "template_summary": {
            f"template_{i}": {"total": 1, "complied": 0, "rate": 0.0, "severity": "INFO"}
            for i in range(template_count)
        },
        "style_summary": {
            f"style_{i}": {"total": 1, "complied": 0, "rate": 0.0, "severity": "INFO"}
            for i in range(style_count)
        },
        "combination_summary": [],
    }
    create_evidence(
        conn,
        type="ipi_sweep_metadata",
        run_id=run_id,
        storage="inline",
        content=json.dumps(blob),
    )
    return run_id


class TestIntelTargetDetailSweepRendering:
    """Sweep Runs section populates on the target detail page."""

    def test_single_sweep_renders_row_and_summary(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="one-sweep")
            run_id = _seed_sweep_run(
                conn,
                target_id,
                finished_at="2026-04-15T12:00:00+00:00",
                template_count=11,
                style_count=3,
                total_cases=33,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert resp.status_code == 200
        assert "Latest sweep:" in text
        assert "11 templates" in text
        assert "3 styles" in text
        assert "N=1" in text  # 33 / (11 * 3) = 1
        assert f'id="sweep-run-{run_id}"' in text
        assert f'href="/runs?run_id={run_id}"' in text

    def test_running_sweep_summary_does_not_render_em_dash_ago(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        """When the most recent sweep has no finished_at, render a status-only summary.

        Regression guard: previously the template piped ``None`` through
        ``format_age`` and rendered "Latest sweep: — ago" for sweeps
        still in RUNNING.
        """
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="running-sweep")
            run_id = create_run(conn, module="ipi-sweep", target_id=target_id)
            update_run_status(conn, run_id, RunStatus.RUNNING)
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert resp.status_code == 200
        assert "Latest sweep:" in text
        assert "\u2014 ago" not in text
        assert "not yet completed" in text

    def test_multiple_sweeps_render_most_recent_first(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="multi-sweep")
            earlier = _seed_sweep_run(conn, target_id, finished_at="2026-04-01T12:00:00+00:00")
            latest = _seed_sweep_run(conn, target_id, finished_at="2026-04-15T12:00:00+00:00")
            mid = _seed_sweep_run(conn, target_id, finished_at="2026-04-05T12:00:00+00:00")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        # Latest should appear before the others in document order.
        latest_pos = text.find(f"sweep-run-{latest}")
        mid_pos = text.find(f"sweep-run-{mid}")
        earlier_pos = text.find(f"sweep-run-{earlier}")
        assert 0 < latest_pos < mid_pos < earlier_pos


class TestIntelTargetDetailGenerateAffordance:
    """Generate button rendering across the five selection-result variants.

    Mocks ``select_template_for_target`` at the detail-handler import site
    so each variant can be exercised without seeding sweep data that
    happens to land on the specific branch. A complementary no-cache
    test below mutates real sweep state between two GETs.
    """

    def _make_target(self, tmp_db: Path) -> str:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="generate-target")
            conn.commit()
        finally:
            conn.close()
        return target_id

    def test_selected_template_fresh_renders_enabled_button(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        from datetime import UTC, datetime

        from q_ai.ipi.models import DocumentTemplate
        from q_ai.ipi.sweep_selection import SelectedTemplate

        target_id = self._make_target(tmp_db)
        result = SelectedTemplate(
            template=DocumentTemplate.WHOIS,
            run_id="run-123",
            completed_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
            compliance_rate=0.85,
            age_days=2,
            stale_warn=False,
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=result,
        ):
            resp = client.get(f"/intel/targets/{target_id}")

        assert resp.status_code == 200
        text = resp.text
        assert 'href="/launcher?' in text
        # Muted variant's class modifier is absent in the fresh path.
        assert 'class="intel-generate-btn intel-generate-btn-muted"' not in text
        assert "target_name=generate-target" in text
        assert f"template={result.template.value}" in text
        assert f"Generate with {result.template.value.upper()}" in text

    def test_selected_template_stale_warn_renders_muted_button(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        from datetime import UTC, datetime

        from q_ai.ipi.models import DocumentTemplate
        from q_ai.ipi.sweep_selection import SelectedTemplate

        target_id = self._make_target(tmp_db)
        result = SelectedTemplate(
            template=DocumentTemplate.WHOIS,
            run_id="run-456",
            completed_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
            compliance_rate=0.72,
            age_days=19,
            stale_warn=True,
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=result,
        ):
            resp = client.get(f"/intel/targets/{target_id}")

        assert resp.status_code == 200
        text = resp.text
        assert 'class="intel-generate-btn intel-generate-btn-muted"' in text
        assert 'href="/launcher?' in text
        assert f"template={result.template.value}" in text
        assert "target_name=generate-target" in text
        assert 'class="intel-generate-age"' in text

    def test_tie_refusal_renders_note_without_button(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        from q_ai.ipi.models import DocumentTemplate
        from q_ai.ipi.sweep_selection import TieRefusal

        target_id = self._make_target(tmp_db)
        result = TieRefusal(
            candidates=[
                (DocumentTemplate.WHOIS, 0.42),
                (DocumentTemplate.REPORT, 0.40),
                (DocumentTemplate.EMAIL, 0.38),
            ],
            run_id="run-tie",
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=result,
        ):
            resp = client.get(f"/intel/targets/{target_id}")

        assert resp.status_code == 200
        text = resp.text
        assert 'href="/launcher?' not in text
        assert "Near-tie across 3 templates" in text
        assert "WHOIS" in text
        assert "REPORT" in text

    def test_stale_refusal_renders_note_without_button(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        from datetime import UTC, datetime

        from q_ai.ipi.sweep_selection import StaleRefusal

        target_id = self._make_target(tmp_db)
        result = StaleRefusal(
            run_id="run-stale",
            completed_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            age_days=109,
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=result,
        ):
            resp = client.get(f"/intel/targets/{target_id}")

        assert resp.status_code == 200
        text = resp.text
        assert 'href="/launcher?' not in text
        assert "109 days old" in text
        assert "rerun" in text.lower()

    def test_no_findings_renders_no_button(self, tmp_db: Path, client: TestClient) -> None:
        from q_ai.ipi.sweep_selection import NoFindings

        target_id = self._make_target(tmp_db)
        result = NoFindings(target_id=target_id)
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=result,
        ):
            resp = client.get(f"/intel/targets/{target_id}")

        assert resp.status_code == 200
        # No generate button and no NoFindings-specific recommend-note (the
        # Sweep Runs section's own empty-state already points at /intel#card-sweep
        # when sweep_runs is empty; the NoFindings branch only paints an
        # extra note when sweep_runs is non-empty).
        assert 'href="/launcher?' not in resp.text
        assert "/intel#card-sweep" in resp.text  # existing empty-state link

    def test_url_encoding_on_button_href(self, tmp_db: Path, client: TestClient) -> None:
        """Special characters in target name are URL-encoded in the href."""
        from datetime import UTC, datetime

        from q_ai.ipi.models import DocumentTemplate
        from q_ai.ipi.sweep_selection import SelectedTemplate

        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="weird name & spaces")
            conn.commit()
        finally:
            conn.close()

        result = SelectedTemplate(
            template=DocumentTemplate.WHOIS,
            run_id="run-enc",
            completed_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
            compliance_rate=0.9,
            age_days=1,
            stale_warn=False,
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=result,
        ):
            resp = client.get(f"/intel/targets/{target_id}")

        text = resp.text
        # Jinja's urlencode filter produces 'weird+name+%26+spaces' or
        # 'weird%20name%20%26%20spaces' depending on backend — either is
        # a valid query-string encoding. Assert the raw ampersand is not
        # in the href target_name slot (would break query-string parsing).
        assert "target_name=weird name" not in text
        assert "& spaces" not in text.split('href="/launcher?')[1].split('"')[0]


class TestIntelTargetDetailSelectionNoCache:
    """Per RFC Decision 5 Semantic Note: re-evaluated on every GET.

    Mutating sweep state between two page loads produces a correspondingly
    different rendering. Pins the "no module-level memoization" invariant.
    """

    def test_button_disappears_when_run_ages_past_refuse_threshold(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        """First GET: fresh run → generate button. Mutate to old → second GET: no button."""
        from datetime import UTC, datetime, timedelta

        from q_ai.ipi.models import DocumentTemplate
        from q_ai.ipi.sweep_selection import SelectedTemplate, StaleRefusal

        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="no-cache-target")
            conn.commit()
        finally:
            conn.close()

        # First GET: fresh selection → button present
        fresh = SelectedTemplate(
            template=DocumentTemplate.WHOIS,
            run_id="run-fresh",
            completed_at=datetime(2026, 4, 19, 12, 0, tzinfo=UTC),
            compliance_rate=0.9,
            age_days=1,
            stale_warn=False,
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=fresh,
        ):
            first = client.get(f"/intel/targets/{target_id}")
        assert first.status_code == 200
        assert 'href="/launcher?' in first.text

        # Second GET: same URL, different selection result → no button
        stale = StaleRefusal(
            run_id="run-fresh",
            completed_at=datetime.now(UTC) - timedelta(days=99),
            age_days=99,
        )
        with patch(
            "q_ai.server.routes.intel.select_template_for_target",
            return_value=stale,
        ):
            second = client.get(f"/intel/targets/{target_id}")
        assert second.status_code == 200
        assert 'href="/launcher?' not in second.text
        assert "99 days old" in second.text


def _seed_probe_run(
    conn: sqlite3.Connection,
    target_id: str,
    *,
    finished_at: str | None,
    overall_severity: str = "MEDIUM",
    total_probes: int = 20,
    total_complied: int = 4,
    overall_rate: float = 0.2,
    categories: int = 3,
    with_metadata: bool = True,
    status: RunStatus = RunStatus.COMPLETED,
) -> str:
    """Create a probe run with an optional matching metadata blob."""
    run_id = create_run(conn, module="ipi-probe", target_id=target_id)
    update_run_status(conn, run_id, status, finished_at=finished_at)
    if with_metadata:
        blob = {
            "model": "test-model",
            "endpoint": "http://localhost:8000/v1",
            "total_probes": total_probes,
            "total_complied": total_complied,
            "overall_compliance_rate": overall_rate,
            "overall_severity": overall_severity,
            "category_summary": {
                f"cat_{i}": {
                    "total": 1,
                    "complied": 0,
                    "rate": 0.0,
                    "severity": "INFO",
                }
                for i in range(categories)
            },
        }
        create_evidence(
            conn,
            type="ipi_probe_metadata",
            run_id=run_id,
            storage="inline",
            content=json.dumps(blob),
        )
    return run_id


class TestIntelTargetDetailProbeRendering:
    """Probe Runs section populates on the target detail page."""

    def test_single_probe_renders_row_and_summary(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="one-probe")
            run_id = _seed_probe_run(
                conn,
                target_id,
                finished_at="2026-04-15T12:00:00+00:00",
                total_probes=20,
                overall_severity="HIGH",
                categories=8,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert resp.status_code == 200
        assert "Latest probe:" in text
        assert "20 probes" in text
        assert "8 categories" in text
        assert "high severity" in text
        assert f'id="probe-run-{run_id}"' in text
        # Intel-originated row links carry the bypass marker.
        assert f"/runs?run_id={run_id}&amp;intel=1" in text

    def test_running_probe_summary_renders_not_yet_completed(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="running-probe")
            _seed_probe_run(
                conn,
                target_id,
                finished_at=None,
                with_metadata=False,
                status=RunStatus.RUNNING,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert resp.status_code == 200
        assert "Latest probe:" in text
        assert "\u2014 ago" not in text
        assert "not yet completed" in text

    def test_metadata_unavailable_row_is_muted(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="no-meta-probe")
            _seed_probe_run(
                conn,
                target_id,
                finished_at="2026-04-15T12:00:00+00:00",
                with_metadata=False,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert resp.status_code == 200
        assert "Latest probe:" in text
        assert "metadata unavailable" in text
        # The populated aggregate phrases must not appear when metadata is missing.
        assert "probes across" not in text

    def test_multiple_probes_render_most_recent_first(
        self, tmp_db: Path, client: TestClient
    ) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="multi-probe")
            earlier = _seed_probe_run(conn, target_id, finished_at="2026-04-01T12:00:00+00:00")
            latest = _seed_probe_run(conn, target_id, finished_at="2026-04-15T12:00:00+00:00")
            mid = _seed_probe_run(conn, target_id, finished_at="2026-04-05T12:00:00+00:00")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        latest_pos = text.find(f"probe-run-{latest}")
        mid_pos = text.find(f"probe-run-{mid}")
        earlier_pos = text.find(f"probe-run-{earlier}")
        assert 0 < latest_pos < mid_pos < earlier_pos


class TestSweepLaunch:
    """POST /api/intel/sweep/launch validates and accepts."""

    _VALID_BODY: ClassVar[dict[str, object]] = {
        "endpoint": "http://localhost:8000/v1",
        "model": "gpt-4o-mini",
        "templates": ["generic"],
        "styles": ["obvious"],
    }

    def test_invalid_json_body(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_non_dict_body(self, client: TestClient) -> None:
        resp = client.post("/api/intel/sweep/launch", json=["a"])
        assert resp.status_code == 422
        assert "object" in resp.json()["detail"]

    def test_missing_endpoint(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "endpoint": ""},
        )
        assert resp.status_code == 422
        assert "endpoint" in resp.json()["detail"]

    def test_missing_model(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "model": ""},
        )
        assert resp.status_code == 422
        assert "model" in resp.json()["detail"]

    def test_empty_templates(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "templates": []},
        )
        assert resp.status_code == 422
        assert "at least one template is required" in resp.json()["detail"]

    def test_empty_styles(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "styles": []},
        )
        assert resp.status_code == 422
        assert "at least one style is required" in resp.json()["detail"]

    def test_unknown_template(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "templates": ["generic", "not-a-template"]},
        )
        assert resp.status_code == 422
        # Detail must be the raw value, not the StrEnum ValueError text
        # (which contains "is not a valid DocumentTemplate" — a
        # CodeQL py/stack-trace-exposure sink).
        detail = resp.json()["detail"]
        assert detail == "unknown template 'not-a-template'"
        assert "DocumentTemplate" not in detail

    def test_unknown_style(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "styles": ["bogus"]},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail == "unknown style 'bogus'"
        assert "PayloadStyle" not in detail

    def test_non_hashable_template_value_returns_422(self, client: TestClient) -> None:
        """JSON objects/arrays inside templates list must not crash with TypeError."""
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "templates": [{}]},
        )
        assert resp.status_code == 422
        assert "unknown template" in resp.json()["detail"]

    def test_non_string_endpoint_returns_422(self, client: TestClient) -> None:
        """Non-string scalar fields return 422, not a 500 AttributeError."""
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "endpoint": 123},
        )
        assert resp.status_code == 422
        assert "endpoint must be a string" in resp.json()["detail"]

    def test_non_string_target_id_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "target_id": []},
        )
        assert resp.status_code == 422
        assert "target_id must be a string" in resp.json()["detail"]

    def test_invalid_payload_type(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "payload_type": "exfil_summary"},
        )
        assert resp.status_code == 422
        assert "callback" in resp.json()["detail"]

    def test_reps_below_one(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "reps": 0},
        )
        assert resp.status_code == 422
        assert "reps" in resp.json()["detail"]

    def test_concurrency_below_one(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "concurrency": 0},
        )
        assert resp.status_code == 422
        assert "concurrency" in resp.json()["detail"]

    def test_nonexistent_target(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/sweep/launch",
            json={**self._VALID_BODY, "target_id": "does-not-exist"},
        )
        assert resp.status_code == 422
        assert "Target not found" in resp.json()["detail"]

    def test_happy_path_no_target(self, client: TestClient) -> None:
        mock_sweep = AsyncMock(return_value=MagicMock())
        with (
            patch("q_ai.ipi.sweep_service.run_sweep", mock_sweep),
            patch("q_ai.ipi.sweep_service.persist_sweep_run"),
        ):
            resp = client.post("/api/intel/sweep/launch", json=self._VALID_BODY)

        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "launched"
        assert data["redirect"] == "/intel"
        assert "run_id" not in data

    def test_happy_path_with_target(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="sweep-target")
            conn.commit()
        finally:
            conn.close()

        mock_sweep = AsyncMock(return_value=MagicMock())
        with (
            patch("q_ai.ipi.sweep_service.run_sweep", mock_sweep),
            patch("q_ai.ipi.sweep_service.persist_sweep_run"),
        ):
            resp = client.post(
                "/api/intel/sweep/launch",
                json={**self._VALID_BODY, "target_id": target_id},
            )

        assert resp.status_code == 202
        assert resp.json()["redirect"] == f"/intel/targets/{target_id}#sweep-runs"

    def test_background_task_is_scheduled(self, client: TestClient) -> None:
        """Handler registers the task in _background_tasks synchronously.

        The registration (``_background_tasks.add(task)``) happens before
        the response is produced, so spying on the set's ``add`` call is
        race-free — unlike asserting that the mocked ``run_sweep`` was
        invoked, which depended on the event loop stepping into the
        coroutine and was macOS-flaky.
        """
        from q_ai.server.routes import intel as intel_module

        real_set = intel_module._background_tasks
        spy = MagicMock(wraps=real_set)
        mock_sweep = AsyncMock(return_value=MagicMock())
        with (
            patch.object(intel_module, "_background_tasks", spy),
            patch("q_ai.ipi.sweep_service.run_sweep", mock_sweep),
            patch("q_ai.ipi.sweep_service.persist_sweep_run"),
        ):
            resp = client.post("/api/intel/sweep/launch", json=self._VALID_BODY)

        assert resp.status_code == 202
        spy.add.assert_called_once()


def _garak_jsonl() -> str:
    """Build a minimal valid Garak JSONL report."""
    lines = [
        json.dumps(
            {
                "entry_type": "start_run setup",
                "garak_version": "0.9.0.13",
                "run_id": "test-run",
                "model_type": "openai",
                "model_name": "gpt-4",
            }
        ),
        json.dumps(
            {
                "entry_type": "eval",
                "eval_id": "eval-001",
                "probe": "promptinject.HijackHateHumansMini",
                "detector": "toxicity.ToxicityClassifier",
                "passed": 2,
                "total": 10,
                "owasp_llm": "LLM01",
            }
        ),
    ]
    return "\n".join(lines) + "\n"


class TestImportPreview:
    """POST /api/intel/import/preview parses without persisting."""

    def test_preview_garak(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/preview",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["finding_count"] >= 1
        assert isinstance(data["findings"], list)
        assert "severity" in data["findings"][0]
        assert "category" in data["findings"][0]
        assert "title" in data["findings"][0]

    def test_preview_unknown_format(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "data.json"
        file_path.write_text("{}", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/preview",
                files={"file": ("data.json", f, "application/json")},
                data={"format": "unknown"},
            )

        assert resp.status_code == 422
        assert "Unknown format" in resp.json()["detail"]


class TestImportCommit:
    """POST /api/intel/import/commit parses and persists."""

    def test_commit_garak(self, client: TestClient, tmp_db: Path, tmp_path: Path) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="commit-garak-target")
            conn.commit()
        finally:
            conn.close()

        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak", "target_id": target_id},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["finding_count"] >= 1
        assert "run_id" in data

    def test_commit_unknown_format(self, client: TestClient, tmp_db: Path, tmp_path: Path) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="commit-unknown-target")
            conn.commit()
        finally:
            conn.close()

        file_path = tmp_path / "data.json"
        file_path.write_text("{}", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("data.json", f, "application/json")},
                data={"format": "unknown", "target_id": target_id},
            )

        assert resp.status_code == 422


class TestProbeLaunch:
    """POST /api/intel/probe/launch validates and accepts."""

    def test_missing_endpoint(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"model": "gpt-4o-mini"},
        )
        assert resp.status_code == 422
        assert "endpoint" in resp.json()["detail"]

    def test_missing_model(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1"},
        )
        assert resp.status_code == 422
        assert "model" in resp.json()["detail"]

    def test_invalid_json_body(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]

    def test_non_dict_json_body(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json=["a", "list"],
        )
        assert resp.status_code == 422
        assert "object" in resp.json()["detail"]

    def test_non_numeric_temperature(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1", "model": "m", "temperature": "hot"},
        )
        assert resp.status_code == 422
        assert "temperature" in resp.json()["detail"]

    def test_non_numeric_concurrency(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1", "model": "m", "concurrency": "many"},
        )
        assert resp.status_code == 422
        assert "concurrency" in resp.json()["detail"]

    def test_concurrency_below_one(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": "http://localhost:8000/v1", "model": "m", "concurrency": 0},
        )
        assert resp.status_code == 422
        assert "concurrency" in resp.json()["detail"]

    def test_non_string_endpoint_returns_422(self, client: TestClient) -> None:
        """Non-string endpoint returns 422, not a 500 AttributeError on .strip()."""
        resp = client.post(
            "/api/intel/probe/launch",
            json={"endpoint": 123, "model": "m"},
        )
        assert resp.status_code == 422
        assert "endpoint must be a string" in resp.json()["detail"]

    def test_happy_path_launches_no_target(self, client: TestClient) -> None:
        mock_probes = [MagicMock()]
        mock_result = MagicMock()
        with (
            patch("q_ai.ipi.probe_service.load_probes", return_value=mock_probes) as m_load,
            patch(
                "q_ai.ipi.probe_service.run_probes",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("q_ai.ipi.probe_service.persist_probe_run"),
        ):
            resp = client.post(
                "/api/intel/probe/launch",
                json={
                    "endpoint": "http://localhost:8000/v1",
                    "model": "gpt-4o-mini",
                },
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "launched"
        assert data["redirect"] == "/intel"
        assert "run_id" not in data
        m_load.assert_called_once()

    def test_happy_path_launches_with_target(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="probe-launch-target")
            conn.commit()
        finally:
            conn.close()

        mock_probes = [MagicMock()]
        mock_result = MagicMock()
        with (
            patch("q_ai.ipi.probe_service.load_probes", return_value=mock_probes),
            patch(
                "q_ai.ipi.probe_service.run_probes",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("q_ai.ipi.probe_service.persist_probe_run"),
        ):
            resp = client.post(
                "/api/intel/probe/launch",
                json={
                    "endpoint": "http://localhost:8000/v1",
                    "model": "gpt-4o-mini",
                    "target_id": target_id,
                },
            )

        assert resp.status_code == 202
        assert resp.json()["redirect"] == f"/intel/targets/{target_id}#probe-runs"

    def test_nonexistent_target_returns_422(self, client: TestClient) -> None:
        """Probe launch rejects an unknown target_id with 422, mirroring sweep.

        Regression guard: Phase 3's redirect target is
        ``/intel/targets/<target_id>#probe-runs``; without this check a
        bogus target_id would 202 into a redirect that 404s while the
        background task's FK insert fails silently.
        """
        resp = client.post(
            "/api/intel/probe/launch",
            json={
                "endpoint": "http://localhost:8000/v1",
                "model": "gpt-4o-mini",
                "target_id": "does-not-exist",
            },
        )
        assert resp.status_code == 422
        assert "Target not found" in resp.json()["detail"]


class TestImportCommitTargetValidation:
    """POST /api/intel/import/commit validates target_id."""

    def test_invalid_target_id_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak", "target_id": "nonexistent-target-id"},
            )

        assert resp.status_code == 422
        assert "Target not found" in resp.json()["detail"]

    def test_missing_target_id_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        """Phase 5 — target_id is required on import commit."""
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 422
        assert resp.json()["detail"] == "target_id is required"

    def test_empty_target_id_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        """Whitespace-only target_id is rejected as empty-after-strip."""
        file_path = tmp_path / "garak.jsonl"
        file_path.write_text(_garak_jsonl(), encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("garak.jsonl", f, "application/jsonl")},
                data={"format": "garak", "target_id": "   "},
            )

        assert resp.status_code == 422
        assert resp.json()["detail"] == "target_id is required"


class TestErrorMessagesAreGeneric:
    """Error responses must not leak exception text."""

    def test_preview_error_is_generic(self, client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "bad.jsonl"
        file_path.write_text("not valid garak data\n", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/preview",
                files={"file": ("bad.jsonl", f, "application/jsonl")},
                data={"format": "garak"},
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # Must be generic — no internal paths or parser class names.
        assert "Parse failed:" not in detail
        assert "Traceback" not in detail

    def test_commit_error_is_generic(
        self, client: TestClient, tmp_db: Path, tmp_path: Path
    ) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="generic-error-target")
            conn.commit()
        finally:
            conn.close()

        file_path = tmp_path / "bad.jsonl"
        file_path.write_text("not valid garak data\n", encoding="utf-8")

        with file_path.open("rb") as f:
            resp = client.post(
                "/api/intel/import/commit",
                files={"file": ("bad.jsonl", f, "application/jsonl")},
                data={"format": "garak", "target_id": target_id},
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "Import failed:" not in detail
        assert "Traceback" not in detail


class TestIntelCreateTargetEndpoint:
    """POST /api/intel/targets/create validates input and persists."""

    def test_valid_minimum_payload_returns_201(self, client: TestClient, tmp_db: Path) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            json={"name": "new-target", "type": "server"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "target_id" in data
        assert data["name"] == "new-target"
        assert data["type"] == "server"

        conn = _open_db(tmp_db)
        try:
            row = conn.execute(
                "SELECT name, type FROM targets WHERE id = ?", (data["target_id"],)
            ).fetchone()
            assert row is not None
            assert row["name"] == "new-target"
            assert row["type"] == "server"
        finally:
            conn.close()

    def test_full_payload_round_trips_uri_and_metadata(
        self, client: TestClient, tmp_db: Path
    ) -> None:
        payload = {
            "name": "full-target",
            "type": "endpoint",
            "uri": "https://example.invalid",
            "metadata": {"owner": "alice", "env": "prod"},
        }
        resp = client.post("/api/intel/targets/create", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["uri"] == "https://example.invalid"
        assert data["metadata"] == {"owner": "alice", "env": "prod"}

        conn = _open_db(tmp_db)
        try:
            row = conn.execute(
                "SELECT uri, metadata FROM targets WHERE id = ?", (data["target_id"],)
            ).fetchone()
            assert row is not None
            assert row["uri"] == "https://example.invalid"
            assert json.loads(row["metadata"]) == {"owner": "alice", "env": "prod"}
        finally:
            conn.close()

    def test_missing_name_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/intel/targets/create", json={"type": "server"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "name is required"

    def test_empty_name_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            json={"name": "   ", "type": "server"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "name is required"

    def test_missing_type_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/intel/targets/create", json={"name": "t"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "type is required"

    def test_non_string_name_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            json={"name": 42, "type": "server"},
        )
        assert resp.status_code == 422
        assert "name" in resp.json()["detail"]

    def test_non_dict_metadata_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            json={"name": "t", "type": "server", "metadata": "not-a-dict"},
        )
        assert resp.status_code == 422
        assert "metadata" in resp.json()["detail"]

    def test_metadata_with_non_string_value_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            json={"name": "t", "type": "server", "metadata": {"k": 1}},
        )
        assert resp.status_code == 422
        assert "metadata" in resp.json()["detail"]

    def test_malformed_json_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_non_object_body_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/intel/targets/create", json=["not", "an", "object"])
        assert resp.status_code == 422

    def test_absent_metadata_omitted_from_response(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intel/targets/create",
            json={"name": "bare", "type": "server"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "uri" not in data
        assert "metadata" not in data

    def test_name_collision_still_returns_201(self, client: TestClient, tmp_db: Path) -> None:
        """Per PD #2 — collisions warn but do not block creation."""
        conn = _open_db(tmp_db)
        try:
            create_target(conn, type="server", name="dup-name")
            conn.commit()
        finally:
            conn.close()

        resp = client.post(
            "/api/intel/targets/create",
            json={"name": "dup-name", "type": "server"},
        )
        assert resp.status_code == 201

        conn = _open_db(tmp_db)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM targets WHERE name = ?", ("dup-name",)
            ).fetchone()
            assert rows["n"] == 2
        finally:
            conn.close()


class TestIntelPageModalAndSentinel:
    """/intel renders the shared modal and the __new__ sentinel option."""

    def test_modal_partial_rendered(self, client: TestClient) -> None:
        resp = client.get("/intel")
        assert resp.status_code == 200
        assert 'id="target-create-modal"' in resp.text
        assert 'id="target-create-form"' in resp.text

    def test_new_sentinel_in_all_three_cards(self, client: TestClient) -> None:
        resp = client.get("/intel")
        text = resp.text
        # Exactly three sentinel options, one per card.
        assert text.count('value="__new__"') == 3

    def test_no_blank_target_option(self, client: TestClient) -> None:
        """Phase 5 removes the `<option value="">No target</option>` in all three cards."""
        resp = client.get("/intel")
        assert "No target" not in resp.text


def _seed_import_run(
    conn: sqlite3.Connection,
    target_id: str,
    *,
    started_at: str,
    finished_at: str | None,
    source: str,
    finding_count: int,
) -> str:
    """Insert an ``import`` module run with N findings."""
    from q_ai.core.db import create_finding
    from q_ai.core.models import Severity

    run_id = create_run(
        conn,
        module="import",
        name=f"{source}-import-run",
        target_id=target_id,
        source=source,
    )
    # update_run_status handles status + finished_at; started_at needs a
    # direct UPDATE (not exposed by the helper) so ordering tests can
    # pin a specific timestamp.
    update_run_status(
        conn,
        run_id=run_id,
        status=RunStatus.COMPLETED,
        finished_at=finished_at,
    )
    conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?",
        (started_at, run_id),
    )
    for i in range(finding_count):
        create_finding(
            conn,
            run_id=run_id,
            module=source,
            category="test",
            severity=Severity.INFO,
            title=f"finding-{i}",
        )
    return run_id


class TestIntelTargetDetailImportsRendering:
    """Imports section populates on the target detail page."""

    def test_empty_state_renders(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="empty-imports")
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        assert resp.status_code == 200
        assert "No imports yet." in resp.text

    def test_single_import_renders_row(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="single-import")
            run_id = _seed_import_run(
                conn,
                target_id,
                started_at="2026-04-15T12:00:00+00:00",
                finished_at="2026-04-15T12:00:10+00:00",
                source="garak",
                finding_count=3,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert resp.status_code == 200
        assert f'id="import-run-{run_id}"' in text
        assert "garak" in text
        assert "3 findings" in text
        assert f'href="/runs?run_id={run_id}"' in text

    def test_rows_ordered_started_at_desc(self, tmp_db: Path, client: TestClient) -> None:
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="ordering-imports")
            older_id = _seed_import_run(
                conn,
                target_id,
                started_at="2026-04-10T12:00:00+00:00",
                finished_at="2026-04-10T12:00:10+00:00",
                source="pyrit",
                finding_count=1,
            )
            newer_id = _seed_import_run(
                conn,
                target_id,
                started_at="2026-04-15T12:00:00+00:00",
                finished_at="2026-04-15T12:00:10+00:00",
                source="garak",
                finding_count=2,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        older_pos = text.find(f"import-run-{older_id}")
        newer_pos = text.find(f"import-run-{newer_id}")
        assert older_pos != -1
        assert newer_pos != -1
        assert newer_pos < older_pos  # newer renders first

    def test_singular_finding_count(self, tmp_db: Path, client: TestClient) -> None:
        """1 finding renders singular form (no trailing 's')."""
        conn = _open_db(tmp_db)
        try:
            target_id = create_target(conn, type="server", name="singular-import")
            _seed_import_run(
                conn,
                target_id,
                started_at="2026-04-15T12:00:00+00:00",
                finished_at="2026-04-15T12:00:10+00:00",
                source="sarif",
                finding_count=1,
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(f"/intel/targets/{target_id}")
        text = resp.text
        assert "1 finding " in text or "1 finding\n" in text or "1 finding<" in text
        # And not the plural form with this exact count.
        assert "1 findings" not in text
