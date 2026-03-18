"""Tests for the framework update checker."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from q_ai.cli import app
from q_ai.core.framework_update import (
    _compute_frameworks_hash,
    check_atlas,
    check_owasp_mcp,
    extract_atlas_techniques,
    get_local_atlas_techniques,
    get_reviewed_version,
    is_cache_valid,
    load_cache,
    run_checks,
    save_cache,
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_FRAMEWORKS_YAML = """\
frameworks:
  mitre_atlas:
    reviewed_against: "4.0"
    last_reviewed: "2026-03-14"
    url: "https://atlas.mitre.org/"
    mappings:
      command_injection: "AML.T0040"
      prompt_injection: "AML.T0051"

  owasp_mcp_top10:
    reviewed_against: "2025-beta"
    last_reviewed: "2026-03-14"
    url: "https://owasp.org/www-project-mcp-top-10/"
    mappings:
      command_injection: "MCP05"
      auth: "MCP07"
"""

SAMPLE_FRAMEWORKS_VERSION_FIELD = """\
frameworks:
  mitre_atlas:
    version: "4.0"
    mappings:
      command_injection: "AML.T0040"
      prompt_injection: "AML.T0051"
  owasp_mcp_top10:
    version: "2025-beta"
    mappings:
      command_injection: "MCP05"
"""

SAMPLE_ATLAS_YAML = {
    "id": "atlas",
    "name": "ATLAS",
    "version": "5.4.0",
    "matrices": [
        {
            "id": "atlas-ml",
            "tactics": [
                {
                    "id": "AML.TA0001",
                    "techniques": [
                        {"id": "AML.T0040", "name": "ML Supply Chain Compromise"},
                        {"id": "AML.T0051", "name": "LLM Prompt Injection"},
                        {"id": "AML.T0099", "name": "New Technique"},
                        {
                            "id": "AML.T0100",
                            "name": "Another New",
                            "subtechniques": [
                                {"id": "AML.T0100.001", "name": "Sub"},
                            ],
                        },
                    ],
                },
            ],
        },
    ],
}

SAMPLE_RELEASE = {
    "tag_name": "v5.4.0",
    "assets": [
        {
            "name": "ATLAS.yaml",
            "browser_download_url": "https://example.com/ATLAS.yaml",
        },
        {
            "name": "README.md",
            "browser_download_url": "https://example.com/README.md",
        },
    ],
}


@pytest.fixture()
def frameworks_yaml(tmp_path: Path) -> Path:
    """Write sample frameworks.yaml and return its path."""
    p = tmp_path / "frameworks.yaml"
    p.write_text(SAMPLE_FRAMEWORKS_YAML)
    return p


@pytest.fixture()
def frameworks_yaml_version_field(tmp_path: Path) -> Path:
    """Write sample using 'version' instead of 'reviewed_against'."""
    p = tmp_path / "frameworks.yaml"
    p.write_text(SAMPLE_FRAMEWORKS_VERSION_FIELD)
    return p


# ---------------------------------------------------------------------------
# ATLAS diff logic
# ---------------------------------------------------------------------------


class TestAtlasDiffLogic:
    """ATLAS technique diff correctly identifies new, unchanged, deprecated."""

    def test_extract_atlas_techniques_from_nested_yaml(self) -> None:
        techniques = extract_atlas_techniques(SAMPLE_ATLAS_YAML)
        assert "AML.T0040" in techniques
        assert "AML.T0051" in techniques
        assert "AML.T0099" in techniques
        assert "AML.T0100" in techniques
        assert "AML.T0100.001" in techniques
        # Tactic IDs should NOT be included
        assert "AML.TA0001" not in techniques

    def test_get_local_atlas_techniques(self, frameworks_yaml: Path) -> None:
        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        techniques = get_local_atlas_techniques(fw)
        assert techniques == {"AML.T0040", "AML.T0051"}

    def test_diff_finds_new_and_deprecated(self, frameworks_yaml: Path) -> None:
        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        local = get_local_atlas_techniques(fw)
        upstream = extract_atlas_techniques(SAMPLE_ATLAS_YAML)

        new = sorted(upstream - local)
        deprecated = sorted(local - upstream)

        assert "AML.T0099" in new
        assert "AML.T0100" in new
        assert "AML.T0100.001" in new
        assert len(deprecated) == 0

    def test_diff_detects_deprecated_technique(self) -> None:
        local = {"AML.T0040", "AML.T0051", "AML.T0999"}
        upstream = {"AML.T0040", "AML.T0051"}
        deprecated = sorted(local - upstream)
        assert deprecated == ["AML.T0999"]

    def test_extract_empty_yaml(self) -> None:
        assert extract_atlas_techniques({}) == set()
        assert extract_atlas_techniques([]) == set()

    def test_reviewed_against_fallback_to_version(
        self, frameworks_yaml_version_field: Path
    ) -> None:
        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml_version_field)
        assert get_reviewed_version(fw["mitre_atlas"]) == "4.0"

    def test_reviewed_against_preferred_over_version(self) -> None:
        fw_data = {"reviewed_against": "5.0", "version": "4.0"}
        assert get_reviewed_version(fw_data) == "5.0"


# ---------------------------------------------------------------------------
# ATLAS check with mocked HTTP
# ---------------------------------------------------------------------------


class TestCheckAtlas:
    """ATLAS check end-to-end with mocked network."""

    @patch("q_ai.core.framework_update._fetch_yaml")
    @patch("q_ai.core.framework_update._fetch_json")
    def test_successful_check(
        self,
        mock_fetch_json: object,
        mock_fetch_yaml: object,
        frameworks_yaml: Path,
    ) -> None:
        mock_fetch_json.return_value = SAMPLE_RELEASE  # type: ignore[attr-defined]
        mock_fetch_yaml.return_value = SAMPLE_ATLAS_YAML  # type: ignore[attr-defined]

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_atlas(fw)

        assert result.error is None
        assert result.upstream_version == "v5.4.0"
        assert result.local_version == "4.0"
        assert "AML.T0099" in result.new_techniques
        assert len(result.deprecated_techniques) == 0

    @patch("q_ai.core.framework_update._fetch_json")
    def test_github_api_unreachable(
        self,
        mock_fetch_json: object,
        frameworks_yaml: Path,
    ) -> None:
        from urllib.error import URLError

        mock_fetch_json.side_effect = URLError("network error")  # type: ignore[attr-defined]

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_atlas(fw)

        assert result.error is not None
        assert "Failed to fetch" in result.error
        assert result.upstream_version is None

    @patch("q_ai.core.framework_update._fetch_yaml")
    @patch("q_ai.core.framework_update._fetch_json")
    def test_atlas_yaml_download_fails(
        self,
        mock_fetch_json: object,
        mock_fetch_yaml: object,
        frameworks_yaml: Path,
    ) -> None:
        from urllib.error import URLError

        mock_fetch_json.return_value = SAMPLE_RELEASE  # type: ignore[attr-defined]
        mock_fetch_yaml.side_effect = URLError("download error")  # type: ignore[attr-defined]

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_atlas(fw)

        assert result.error is not None
        assert "Failed to download" in result.error
        assert result.upstream_version == "v5.4.0"

    @patch("q_ai.core.framework_update._fetch_json")
    def test_no_atlas_yaml_asset_in_release(
        self,
        mock_fetch_json: object,
        frameworks_yaml: Path,
    ) -> None:
        release_no_yaml = {
            "tag_name": "v5.4.0",
            "assets": [{"name": "README.md", "browser_download_url": "https://x.com/r"}],
        }
        mock_fetch_json.return_value = release_no_yaml  # type: ignore[attr-defined]

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_atlas(fw)

        assert result.error is not None
        assert "not found" in result.error


# ---------------------------------------------------------------------------
# OWASP MCP check
# ---------------------------------------------------------------------------


class TestCheckOwaspMcp:
    """OWASP MCP Top 10 version check."""

    @patch("q_ai.core.framework_update._fetch_text")
    def test_version_unchanged(
        self,
        mock_fetch: object,
        frameworks_yaml: Path,
    ) -> None:
        mock_fetch.return_value = (  # type: ignore[attr-defined]
            "<html>Version: 2025-beta</html>"
        )

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_owasp_mcp(fw)

        assert result.error is None
        assert not result.version_changed
        assert not result.needs_review

    @patch("q_ai.core.framework_update._fetch_text")
    def test_version_changed(
        self,
        mock_fetch: object,
        frameworks_yaml: Path,
    ) -> None:
        mock_fetch.return_value = (  # type: ignore[attr-defined]
            "<html>Version: 2026-rc1</html>"
        )

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_owasp_mcp(fw)

        assert result.version_changed
        assert result.needs_review

    @patch("q_ai.core.framework_update._fetch_text")
    def test_owasp_page_unreachable(
        self,
        mock_fetch: object,
        frameworks_yaml: Path,
    ) -> None:
        from urllib.error import URLError

        mock_fetch.side_effect = URLError("network error")  # type: ignore[attr-defined]

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_owasp_mcp(fw)

        assert result.error is not None
        assert "Failed to fetch" in result.error

    @patch("q_ai.core.framework_update._fetch_text")
    def test_no_version_marker_on_page(
        self,
        mock_fetch: object,
        frameworks_yaml: Path,
    ) -> None:
        mock_fetch.return_value = (  # type: ignore[attr-defined]
            "<html>No version info here</html>"
        )

        from q_ai.core.framework_update import load_frameworks_metadata

        fw = load_frameworks_metadata(frameworks_yaml)
        result = check_owasp_mcp(fw)

        assert result.needs_review
        assert result.error is not None
        assert "manual review" in result.error.lower()


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCache:
    """Cache TTL and invalidation tests."""

    def test_cache_used_on_subsequent_runs(self, tmp_path: Path) -> None:
        cache_data = {
            "timestamp": time.time(),
            "frameworks_hash": "abc123",
            "atlas": {
                "upstream_version": "v5.4.0",
                "new_techniques": ["AML.T0099"],
                "deprecated_techniques": [],
                "error": None,
            },
            "owasp_mcp": {
                "upstream_version": "2025-beta",
                "version_changed": False,
                "needs_review": False,
                "error": None,
            },
        }
        assert is_cache_valid(cache_data, "abc123")

    def test_cache_expired(self) -> None:
        cache_data = {
            "timestamp": time.time() - 90000,  # > 24 hours
            "frameworks_hash": "abc123",
        }
        assert not is_cache_valid(cache_data, "abc123")

    def test_cache_invalidated_when_reviewed_against_changes(
        self,
    ) -> None:
        fw_a = {
            "mitre_atlas": {"reviewed_against": "4.0"},
            "owasp_mcp_top10": {"reviewed_against": "2025-beta"},
        }
        fw_b = {
            "mitre_atlas": {"reviewed_against": "5.0"},
            "owasp_mcp_top10": {"reviewed_against": "2025-beta"},
        }
        hash_a = _compute_frameworks_hash(fw_a)
        hash_b = _compute_frameworks_hash(fw_b)
        assert hash_a != hash_b

        cache = {"timestamp": time.time(), "frameworks_hash": hash_a}
        assert is_cache_valid(cache, hash_a)
        assert not is_cache_valid(cache, hash_b)

    def test_cache_invalidated_when_last_reviewed_changes(self) -> None:
        fw_a = {
            "mitre_atlas": {
                "reviewed_against": "4.0",
                "last_reviewed": "2026-03-14",
            },
        }
        fw_b = {
            "mitre_atlas": {
                "reviewed_against": "4.0",
                "last_reviewed": "2026-03-18",
            },
        }
        hash_a = _compute_frameworks_hash(fw_a)
        hash_b = _compute_frameworks_hash(fw_b)
        assert hash_a != hash_b

    def test_save_and_load_cache(self, tmp_path: Path) -> None:
        with (
            patch(
                "q_ai.core.framework_update.CACHE_FILE",
                tmp_path / "cache.json",
            ),
            patch(
                "q_ai.core.framework_update.CACHE_DIR",
                tmp_path,
            ),
        ):
            data = {"timestamp": time.time(), "frameworks_hash": "test"}
            save_cache(data)
            loaded = load_cache()
            assert loaded is not None
            assert loaded["frameworks_hash"] == "test"

    def test_load_cache_returns_none_when_missing(self, tmp_path: Path) -> None:
        with patch(
            "q_ai.core.framework_update.CACHE_FILE",
            tmp_path / "nonexistent.json",
        ):
            assert load_cache() is None

    @patch("q_ai.core.framework_update._fetch_text")
    @patch("q_ai.core.framework_update._fetch_yaml")
    @patch("q_ai.core.framework_update._fetch_json")
    def test_run_checks_uses_cache(
        self,
        mock_json: object,
        mock_yaml: object,
        mock_text: object,
        frameworks_yaml: Path,
        tmp_path: Path,
    ) -> None:
        """Second run within TTL should use cache, not fetch again."""
        mock_json.return_value = SAMPLE_RELEASE  # type: ignore[attr-defined]
        mock_yaml.return_value = SAMPLE_ATLAS_YAML  # type: ignore[attr-defined]
        mock_text.return_value = "<html>Version: 2025-beta</html>"  # type: ignore[attr-defined]

        cache_file = tmp_path / "cache.json"
        with (
            patch("q_ai.core.framework_update.CACHE_FILE", cache_file),
            patch("q_ai.core.framework_update.CACHE_DIR", tmp_path),
        ):
            # First run: fetches from network
            atlas1, _owasp1 = run_checks(yaml_path=frameworks_yaml)
            assert not atlas1.from_cache
            assert mock_json.call_count == 1  # type: ignore[attr-defined]

            # Second run: uses cache
            atlas2, owasp2 = run_checks(yaml_path=frameworks_yaml)
            assert atlas2.from_cache
            assert owasp2.from_cache
            # No additional network calls
            assert mock_json.call_count == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


class TestUpdateFrameworksCli:
    """CLI command registration and help."""

    def test_help_shows_usage(self) -> None:
        result = runner.invoke(app, ["update-frameworks", "--help"])
        assert result.exit_code == 0
        assert "update-frameworks" in result.output
        assert "--atlas" in result.output
        assert "--no-cache" in result.output

    def test_root_help_shows_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "update-frameworks" in result.output

    @patch("q_ai.core.framework_update._fetch_text")
    @patch("q_ai.core.framework_update._fetch_yaml")
    @patch("q_ai.core.framework_update._fetch_json")
    def test_runs_without_error(
        self,
        mock_json: object,
        mock_yaml: object,
        mock_text: object,
        frameworks_yaml: Path,
        tmp_path: Path,
    ) -> None:
        mock_json.return_value = SAMPLE_RELEASE  # type: ignore[attr-defined]
        mock_yaml.return_value = SAMPLE_ATLAS_YAML  # type: ignore[attr-defined]
        mock_text.return_value = "<html>Version: 2025-beta</html>"  # type: ignore[attr-defined]

        cache_file = tmp_path / "cache.json"
        with (
            patch("q_ai.core.framework_update.CACHE_FILE", cache_file),
            patch("q_ai.core.framework_update.CACHE_DIR", tmp_path),
        ):
            result = runner.invoke(
                app,
                [
                    "update-frameworks",
                    "--no-cache",
                    "--yaml-path",
                    str(frameworks_yaml),
                ],
            )
        assert result.exit_code == 0
        assert "MITRE ATLAS" in result.output
        assert "OWASP MCP Top 10" in result.output

    @patch("q_ai.core.framework_update._fetch_text")
    @patch("q_ai.core.framework_update._fetch_yaml")
    @patch("q_ai.core.framework_update._fetch_json")
    def test_atlas_flag_shows_diff(
        self,
        mock_json: object,
        mock_yaml: object,
        mock_text: object,
        frameworks_yaml: Path,
        tmp_path: Path,
    ) -> None:
        mock_json.return_value = SAMPLE_RELEASE  # type: ignore[attr-defined]
        mock_yaml.return_value = SAMPLE_ATLAS_YAML  # type: ignore[attr-defined]
        mock_text.return_value = "<html>Version: 2025-beta</html>"  # type: ignore[attr-defined]

        cache_file = tmp_path / "cache.json"
        with (
            patch("q_ai.core.framework_update.CACHE_FILE", cache_file),
            patch("q_ai.core.framework_update.CACHE_DIR", tmp_path),
        ):
            result = runner.invoke(
                app,
                [
                    "update-frameworks",
                    "--atlas",
                    "--no-cache",
                    "--yaml-path",
                    str(frameworks_yaml),
                ],
            )
        assert result.exit_code == 0
        assert "AML.T0099" in result.output

    @patch("q_ai.core.framework_update._fetch_json")
    def test_graceful_on_network_failure(
        self,
        mock_json: object,
        frameworks_yaml: Path,
        tmp_path: Path,
    ) -> None:
        from urllib.error import URLError

        mock_json.side_effect = URLError("offline")  # type: ignore[attr-defined]

        cache_file = tmp_path / "cache.json"
        with (
            patch("q_ai.core.framework_update.CACHE_FILE", cache_file),
            patch("q_ai.core.framework_update.CACHE_DIR", tmp_path),
            patch(
                "q_ai.core.framework_update._fetch_text",
                side_effect=URLError("offline"),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "update-frameworks",
                    "--no-cache",
                    "--yaml-path",
                    str(frameworks_yaml),
                ],
            )
        # Should not crash
        assert result.exit_code == 0
        assert "MITRE ATLAS" in result.output
