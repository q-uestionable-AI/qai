"""Tests for GET /api/ipi/templates."""

from __future__ import annotations

from fastapi.testclient import TestClient

_REQUIRED_FIELDS = {
    "id",
    "name",
    "description",
    "source_tool",
    "source_reference",
    "formats",
    "default_style",
}

_ALLOWED_SOURCE_TOOLS = {"garak", "bipia", "generic"}


class TestIpiTemplatesEndpoint:
    """GET /api/ipi/templates surfaces the IPI template registry."""

    def test_returns_200_and_envelope(self, client: TestClient) -> None:
        """Endpoint returns 200 with a ``templates`` array envelope."""
        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        body = resp.json()
        assert "templates" in body
        assert isinstance(body["templates"], list)

    def test_returns_every_registered_template(self, client: TestClient) -> None:
        """Endpoint returns every template registered in ``TEMPLATE_REGISTRY``."""
        from q_ai.ipi.template_registry import TEMPLATE_REGISTRY

        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert len(templates) == len(TEMPLATE_REGISTRY)
        assert {t["id"] for t in templates} == {k.value for k in TEMPLATE_REGISTRY}

    def test_generic_is_first(self, client: TestClient) -> None:
        """Registry order is preserved — GENERIC comes first."""
        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert templates[0]["id"] == "generic"

    def test_required_fields_present(self, client: TestClient) -> None:
        """Every entry exposes the public metadata fields."""
        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        for entry in templates:
            missing = _REQUIRED_FIELDS - entry.keys()
            assert not missing, f"{entry.get('id')!r} missing {missing}"

    def test_formats_is_list_of_strings(self, client: TestClient) -> None:
        """Each entry's ``formats`` field is a list of format-value strings."""
        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        for entry in templates:
            assert isinstance(entry["formats"], list)
            assert entry["formats"], f"{entry['id']!r} has no declared formats"
            for fmt in entry["formats"]:
                assert isinstance(fmt, str)

    def test_source_tool_is_allowed_value(self, client: TestClient) -> None:
        """Every entry's ``source_tool`` is one of garak/bipia/generic."""
        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        for entry in templates:
            assert entry["source_tool"] in _ALLOWED_SOURCE_TOOLS, entry

    def test_internal_fields_not_exposed(self, client: TestClient) -> None:
        """Internal registry fields stay server-side."""
        resp = client.get("/api/ipi/templates")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        for entry in templates:
            assert "top_instruction" not in entry
            assert "context_template" not in entry
            assert "source_commit" not in entry
