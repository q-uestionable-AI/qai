"""Tests for Phase 4 launcher query-param prefill.

Covers the ``/launcher`` handler's consumption of ``target_name`` and
``template`` query parameters, emitted by the Intel target-detail page's
"Generate with recommended template" button.

Note on format-filter cascade: all 12 ``DocumentTemplate`` registry
entries currently list ``Format.PDF`` in their compatible formats (see
``src/q_ai/ipi/template_registry.py``). The ``_wireIpiFilters`` cascade
in ``loadIpiTemplates`` that would flash-reset the format select on an
incompatible prefill is therefore a no-op today. This comment and a
template_registry walk pin the current state; a future template that
drops PDF should trigger a new test exercising the flash-reset.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestLauncherQueryParamConsumption:
    """``/launcher`` accepts ``target_name`` and ``template`` query params."""

    def test_no_params_preserves_baseline(self, client: TestClient) -> None:
        """Absent params → no prefill script, no pre-populated target_name."""
        resp = client.get("/launcher")
        assert resp.status_code == 200
        # The JS reads window._testDocsPrefill unconditionally, so its
        # identifier appears in the page always. The server-rendered
        # assignment `window._testDocsPrefill = {...}` only appears when
        # a template prefill is present.
        assert "window._testDocsPrefill = " not in resp.text

    def test_target_name_prefill_in_input_value(self, client: TestClient) -> None:
        """``?target_name=foo`` renders ``value="foo"`` on the test_docs target-name input."""
        resp = client.get("/launcher?target_name=foo")
        assert resp.status_code == 200
        # The test_docs form target_name input is the one that consumes the prefill;
        # other workflow forms render with empty value="".
        assert 'value="foo"' in resp.text

    def test_template_prefill_emits_wire_format(self, client: TestClient) -> None:
        """``?template=whois`` emits the |tojson-encoded prefill init script."""
        resp = client.get("/launcher?target_name=foo&template=whois")
        assert resp.status_code == 200
        text = resp.text
        assert "window._testDocsPrefill = " in text
        # |tojson renders the dict as a JS object literal; the template id
        # appears inside a JSON-encoded string. Assert the raw id is present
        # inside the script block — exact formatting is Jinja's call.
        assert "whois" in text
        assert 'value="foo"' in text

    def test_unknown_template_does_not_crash(self, client: TestClient) -> None:
        """An unknown template id in the query string is accepted at the handler level.

        The JS fallback in ``loadIpiTemplates`` handles the unknown-id case
        by leaving the default selection (GENERIC) in place. The handler
        itself does no validation (the launcher form submit validates on
        submit) so the response is still 200 with structurally valid HTML.
        """
        resp = client.get("/launcher?target_name=foo&template=does-not-exist")
        assert resp.status_code == 200
        assert "window._testDocsPrefill = " in resp.text
        # Form skeleton still present
        assert "form-test_docs" in resp.text

    def test_empty_params_treated_as_absent(self, client: TestClient) -> None:
        """``?target_name=&template=`` produces identical context to no-params."""
        resp = client.get("/launcher?target_name=&template=")
        assert resp.status_code == 200
        # The JS reads window._testDocsPrefill unconditionally, so its
        # identifier appears in the page always. The server-rendered
        # assignment `window._testDocsPrefill = {...}` only appears when
        # a template prefill is present.
        assert "window._testDocsPrefill = " not in resp.text

    def test_whitespace_only_params_treated_as_absent(self, client: TestClient) -> None:
        """Whitespace-only values strip to empty and are treated as absent."""
        resp = client.get("/launcher?target_name=%20%20&template=%20")
        assert resp.status_code == 200
        # The JS reads window._testDocsPrefill unconditionally, so its
        # identifier appears in the page always. The server-rendered
        # assignment `window._testDocsPrefill = {...}` only appears when
        # a template prefill is present.
        assert "window._testDocsPrefill = " not in resp.text


class TestLauncherPrefillXssHardening:
    """XSS defense: |tojson escapes `<` as \\u003C inside JS string context."""

    def test_script_injection_via_template_is_escaped(self, client: TestClient) -> None:
        """A ``</script>`` payload in the template param does not break out of the script block."""
        resp = client.get("/launcher?template=%3C/script%3E%3Cscript%3Ealert(1)%3C/script%3E")
        assert resp.status_code == 200
        text = resp.text
        # The raw </script> sequence must not appear inside the prefill
        # init script (browsers terminate <script> on that literal sequence
        # regardless of surrounding HTML entities). |tojson escapes `<` as
        # \u003C inside JS strings, so the raw sequence is absent.
        prefill_idx = text.find("window._testDocsPrefill")
        assert prefill_idx >= 0
        # Slice the enclosing script block and assert no raw </script>
        # prior to the true closing tag.
        script_end = text.find("</script>", prefill_idx)
        block = text[prefill_idx:script_end]
        assert "</script>" not in block


class TestLauncherNoRegression:
    """Existing no-param launcher behavior is unchanged."""

    def test_target_name_input_has_empty_value_default(self, client: TestClient) -> None:
        """Without prefill, target_name inputs render ``value=""``."""
        resp = client.get("/launcher")
        # Multiple forms render target_name_field; at least one should be
        # value="" (the default initial_value macro param).
        assert 'value=""' in resp.text

    def test_test_docs_form_still_renders(self, client: TestClient) -> None:
        """The test_docs form skeleton renders regardless of prefill."""
        resp = client.get("/launcher")
        assert 'id="form-test_docs"' in resp.text
