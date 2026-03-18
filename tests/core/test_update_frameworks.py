"""Tests for framework update check business logic."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from q_ai.core.update_frameworks import (
    AtlasDiff,
    FrameworkStatus,
    _compute_invalidation_key,
    _detect_owasp_version,
    _dict_to_status,
    _extract_local_atlas_ids,
    _extract_upstream_atlas_ids,
    _find_atlas_asset_url,
    _load_cache,
    _save_cache,
    _status_to_dict,
    check_atlas,
    check_frameworks,
    check_owasp_mcp,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_FRAMEWORKS = {
    "mitre_atlas": {
        "reviewed_against": "v5.4.0",
        "last_reviewed": "2026-03-18",
        "url": "https://atlas.mitre.org/",
        "mappings": {
            "command_injection": ["AML.T0043", "AML.T0050"],
            "tool_poisoning": ["AML.T0051.000", "AML.T0080", "AML.T0099"],
            "prompt_injection": ["AML.T0051.000", "AML.T0051.001"],
        },
    },
    "owasp_mcp_top10": {
        "version": "2025-beta",
        "url": "https://owasp.org/www-project-mcp-top-10/",
        "mappings": {
            "command_injection": "MCP05",
        },
    },
}

SAMPLE_RELEASE_RESPONSE = {
    "tag_name": "v5.5.0",
    "assets": [
        {
            "name": "ATLAS.yaml",
            "browser_download_url": "https://github.com/mitre-atlas/atlas-data/releases/download/v5.5.0/ATLAS.yaml",
        },
        {
            "name": "checksums.txt",
            "browser_download_url": "https://example.com/checksums.txt",
        },
    ],
}

SAMPLE_ATLAS_YAML = b"""
id: atlas
name: ATLAS
techniques:
  - id: AML.T0043
    name: Existing Technique
  - id: AML.T0050
    name: Another Existing
  - id: AML.T0051.000
    name: Subtechnique
  - id: AML.T0051.001
    name: Another Subtechnique
  - id: AML.T0080
    name: Still Here
  - id: AML.T0099
    name: Still Present
  - id: AML.T0100
    name: Brand New Technique
  - id: AML.T0101
    name: Another New One
"""


# ---------------------------------------------------------------------------
# ATLAS diff logic
# ---------------------------------------------------------------------------


class TestExtractLocalAtlasIds:
    """_extract_local_atlas_ids collects all ATLAS technique IDs from mappings."""

    def test_extracts_all_unique_ids(self) -> None:
        ids = _extract_local_atlas_ids(SAMPLE_FRAMEWORKS)
        expected = {
            "AML.T0043",
            "AML.T0050",
            "AML.T0051.000",
            "AML.T0051.001",
            "AML.T0080",
            "AML.T0099",
        }
        assert ids == expected

    def test_handles_string_values(self) -> None:
        frameworks = {
            "mitre_atlas": {
                "mappings": {"single": "AML.T0001"},
            },
        }
        assert _extract_local_atlas_ids(frameworks) == {"AML.T0001"}

    def test_handles_missing_atlas(self) -> None:
        assert _extract_local_atlas_ids({}) == set()


class TestExtractUpstreamAtlasIds:
    """_extract_upstream_atlas_ids parses ATLAS.yaml and extracts technique IDs."""

    def test_extracts_technique_ids(self) -> None:
        ids = _extract_upstream_atlas_ids(SAMPLE_ATLAS_YAML)
        assert "AML.T0043" in ids
        assert "AML.T0100" in ids
        assert "AML.T0101" in ids

    def test_extracts_subtechniques(self) -> None:
        ids = _extract_upstream_atlas_ids(SAMPLE_ATLAS_YAML)
        assert "AML.T0051.000" in ids
        assert "AML.T0051.001" in ids

    def test_ignores_non_technique_ids(self) -> None:
        data = b"""
id: atlas
name: ATLAS
tactics:
  - id: AML.TA0001
    name: Not A Technique
techniques:
  - id: AML.T0001
    name: Real Technique
"""
        ids = _extract_upstream_atlas_ids(data)
        assert "AML.T0001" in ids
        assert "AML.TA0001" not in ids


class TestAtlasDiff:
    """check_atlas correctly identifies new, unchanged, and deprecated techniques."""

    @patch("q_ai.core.update_frameworks._http_get")
    def test_identifies_new_and_deprecated(self, mock_http: object) -> None:
        """New techniques in upstream not in local, local techniques not in upstream."""
        # Upstream has T0100, T0101 (new) but missing nothing from local
        # Actually our sample ATLAS.yaml includes all local IDs plus T0100, T0101
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = [
            json.dumps(SAMPLE_RELEASE_RESPONSE).encode(),
            SAMPLE_ATLAS_YAML,
        ]

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "update-available"
        assert result.atlas_diff is not None
        assert "AML.T0100" in result.atlas_diff.new_techniques
        assert "AML.T0101" in result.atlas_diff.new_techniques

    @patch("q_ai.core.update_frameworks._http_get")
    def test_identifies_deprecated_techniques(self, mock_http: object) -> None:
        """Techniques in local but not in upstream are deprecated."""
        # Upstream YAML missing AML.T0099
        atlas_yaml_missing = b"""
techniques:
  - id: AML.T0043
  - id: AML.T0050
  - id: AML.T0051.000
  - id: AML.T0051.001
  - id: AML.T0080
"""
        release = {**SAMPLE_RELEASE_RESPONSE, "tag_name": "v5.5.0"}
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = [
            json.dumps(release).encode(),
            atlas_yaml_missing,
        ]

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "update-available"
        assert result.atlas_diff is not None
        assert "AML.T0099" in result.atlas_diff.deprecated_techniques

    @patch("q_ai.core.update_frameworks._http_get")
    def test_up_to_date_when_versions_and_ids_match(self, mock_http: object) -> None:
        """Reports up-to-date when version matches and no diff."""
        atlas_yaml_exact = b"""
techniques:
  - id: AML.T0043
  - id: AML.T0050
  - id: AML.T0051.000
  - id: AML.T0051.001
  - id: AML.T0080
  - id: AML.T0099
"""
        release = {**SAMPLE_RELEASE_RESPONSE, "tag_name": "v5.4.0"}
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = [
            json.dumps(release).encode(),
            atlas_yaml_exact,
        ]

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "up-to-date"

    @patch("q_ai.core.update_frameworks._http_get")
    def test_version_delta_reported(self, mock_http: object) -> None:
        """Reports version delta even if technique IDs are the same."""
        atlas_yaml_exact = b"""
techniques:
  - id: AML.T0043
  - id: AML.T0050
  - id: AML.T0051.000
  - id: AML.T0051.001
  - id: AML.T0080
  - id: AML.T0099
"""
        release = {**SAMPLE_RELEASE_RESPONSE, "tag_name": "v5.5.0"}
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = [
            json.dumps(release).encode(),
            atlas_yaml_exact,
        ]

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "update-available"
        assert "v5.4.0" in result.message
        assert "v5.5.0" in result.message


# ---------------------------------------------------------------------------
# ATLAS error handling
# ---------------------------------------------------------------------------


class TestAtlasErrorHandling:
    """check_atlas handles external failures gracefully."""

    @patch("q_ai.core.update_frameworks._http_get")
    def test_github_api_unreachable(self, mock_http: object) -> None:
        """Graceful failure when GitHub API is unreachable."""
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = urllib.error.URLError("Connection refused")

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "error"
        assert "Failed to fetch" in result.message

    @patch("q_ai.core.update_frameworks._http_get")
    def test_atlas_yaml_download_fails(self, mock_http: object) -> None:
        """Graceful failure when ATLAS.yaml download fails."""
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = [
            json.dumps(SAMPLE_RELEASE_RESPONSE).encode(),
            urllib.error.URLError("Download failed"),
        ]

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "error"
        assert "Failed to download" in result.message

    @patch("q_ai.core.update_frameworks._http_get")
    def test_no_atlas_asset_in_release(self, mock_http: object) -> None:
        """Graceful failure when release has no ATLAS.yaml asset."""
        release_no_asset = {
            "tag_name": "v5.5.0",
            "assets": [{"name": "other.txt", "browser_download_url": "https://example.com"}],
        }
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.return_value = json.dumps(release_no_asset).encode()

        result = check_atlas(SAMPLE_FRAMEWORKS)
        assert result.status == "error"
        assert "not found" in result.message


# ---------------------------------------------------------------------------
# OWASP MCP Top 10 check
# ---------------------------------------------------------------------------


class TestOwaspMcpCheck:
    """check_owasp_mcp detects version changes."""

    @patch("q_ai.core.update_frameworks._http_get")
    def test_version_unchanged(self, mock_http: object) -> None:
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.return_value = b"<html>Version: 2025-beta</html>"

        result = check_owasp_mcp(SAMPLE_FRAMEWORKS)
        assert result.status == "up-to-date"

    @patch("q_ai.core.update_frameworks._http_get")
    def test_version_changed(self, mock_http: object) -> None:
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.return_value = b"<html>Version: 2026-final</html>"

        result = check_owasp_mcp(SAMPLE_FRAMEWORKS)
        assert result.status == "update-available"
        assert "manual review" in result.message.lower()

    @patch("q_ai.core.update_frameworks._http_get")
    def test_no_version_marker_found(self, mock_http: object) -> None:
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.return_value = b"<html>No version info here at all</html>"

        result = check_owasp_mcp(SAMPLE_FRAMEWORKS)
        assert result.status == "update-available"
        assert "manual review" in result.message.lower()

    @patch("q_ai.core.update_frameworks._http_get")
    def test_page_unreachable(self, mock_http: object) -> None:
        """Graceful failure when OWASP page is unreachable."""
        mock_http_fn = mock_http  # type: ignore[assignment]
        mock_http_fn.side_effect = urllib.error.URLError("Connection refused")

        result = check_owasp_mcp(SAMPLE_FRAMEWORKS)
        assert result.status == "error"
        assert "Failed to fetch" in result.message


# ---------------------------------------------------------------------------
# OWASP version detection
# ---------------------------------------------------------------------------


class TestDetectOwaspVersion:
    """_detect_owasp_version extracts version markers from HTML."""

    def test_detects_year_based_version(self) -> None:
        assert _detect_owasp_version("Version: 2025-beta") == "2025-beta"

    def test_detects_semver(self) -> None:
        assert _detect_owasp_version("Version: 1.0.0") == "1.0.0"

    def test_returns_none_when_no_version(self) -> None:
        assert _detect_owasp_version("Nothing here") is None


# ---------------------------------------------------------------------------
# Asset URL discovery
# ---------------------------------------------------------------------------


class TestFindAtlasAssetUrl:
    """_find_atlas_asset_url discovers the YAML asset from release data."""

    def test_finds_yaml_asset(self) -> None:
        url = _find_atlas_asset_url(SAMPLE_RELEASE_RESPONSE)
        assert url is not None
        assert "ATLAS.yaml" in url

    def test_finds_yml_extension(self) -> None:
        release = {
            "assets": [
                {"name": "atlas-data.yml", "browser_download_url": "https://example.com/a.yml"},
            ],
        }
        assert _find_atlas_asset_url(release) == "https://example.com/a.yml"

    def test_returns_none_when_no_match(self) -> None:
        release = {"assets": [{"name": "other.txt", "browser_download_url": "https://x.com"}]}
        assert _find_atlas_asset_url(release) is None

    def test_handles_empty_assets(self) -> None:
        assert _find_atlas_asset_url({"assets": []}) is None

    def test_handles_missing_assets_key(self) -> None:
        assert _find_atlas_asset_url({}) is None


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCache:
    """Cache is used on subsequent runs within TTL and invalidated correctly."""

    def test_cache_round_trip(self, tmp_path: Path) -> None:
        """Cache saves and loads correctly within TTL."""
        cache_file = tmp_path / "cache" / "framework_updates.json"
        key = "test-key-123"

        results = [
            _status_to_dict(
                FrameworkStatus(
                    framework="mitre_atlas",
                    local_version="v5.4.0",
                    upstream_version="v5.5.0",
                    status="update-available",
                    message="test",
                    atlas_diff=AtlasDiff(new_techniques=["AML.T0100"], deprecated_techniques=[]),
                )
            ),
        ]

        with patch("q_ai.core.update_frameworks._get_cache_path", return_value=cache_file):
            _save_cache(results, key)
            loaded = _load_cache(key)

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["framework"] == "mitre_atlas"
        assert loaded[0]["atlas_diff"]["new_techniques"] == ["AML.T0100"]

    def test_cache_invalidated_on_key_change(self, tmp_path: Path) -> None:
        """Cache is invalidated when invalidation key changes."""
        cache_file = tmp_path / "cache" / "framework_updates.json"
        results = [_status_to_dict(FrameworkStatus("x", "v1", "v2", "up-to-date"))]

        with patch("q_ai.core.update_frameworks._get_cache_path", return_value=cache_file):
            _save_cache(results, "old-key")
            loaded = _load_cache("new-key")

        assert loaded is None

    def test_cache_expired(self, tmp_path: Path) -> None:
        """Cache is not used after TTL expires."""
        cache_file = tmp_path / "cache" / "framework_updates.json"
        key = "test-key"

        with patch("q_ai.core.update_frameworks._get_cache_path", return_value=cache_file):
            _save_cache([_status_to_dict(FrameworkStatus("x", "v1", "v2", "ok"))], key)

            # Tamper with the timestamp to simulate expiry
            with cache_file.open() as f:
                data = json.load(f)
            expired_time = datetime.now(tz=UTC) - timedelta(hours=25)
            data["cached_at"] = expired_time.isoformat()
            with cache_file.open("w") as f:
                json.dump(data, f)

            loaded = _load_cache(key)

        assert loaded is None

    def test_cache_missing_file(self, tmp_path: Path) -> None:
        """Returns None when cache file does not exist."""
        cache_file = tmp_path / "nonexistent" / "cache.json"
        with patch("q_ai.core.update_frameworks._get_cache_path", return_value=cache_file):
            assert _load_cache("any-key") is None

    def test_invalidation_key_changes_on_reviewed_against(self) -> None:
        """Cache invalidation key changes when reviewed_against changes."""
        fw1 = {"mitre_atlas": {"reviewed_against": "v5.4.0", "last_reviewed": "2026-03-18"}}
        fw2 = {"mitre_atlas": {"reviewed_against": "v5.5.0", "last_reviewed": "2026-03-18"}}
        assert _compute_invalidation_key(fw1) != _compute_invalidation_key(fw2)

    def test_invalidation_key_changes_on_last_reviewed(self) -> None:
        """Cache invalidation key changes when last_reviewed changes."""
        fw1 = {"mitre_atlas": {"reviewed_against": "v5.4.0", "last_reviewed": "2026-03-18"}}
        fw2 = {"mitre_atlas": {"reviewed_against": "v5.4.0", "last_reviewed": "2026-03-19"}}
        assert _compute_invalidation_key(fw1) != _compute_invalidation_key(fw2)


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestSerialization:
    """FrameworkStatus round-trips through JSON serialization."""

    def test_round_trip_with_diff(self) -> None:
        status = FrameworkStatus(
            framework="mitre_atlas",
            local_version="v5.4.0",
            upstream_version="v5.5.0",
            status="update-available",
            message="test",
            atlas_diff=AtlasDiff(
                new_techniques=["AML.T0100"],
                deprecated_techniques=["AML.T0001"],
            ),
        )
        restored = _dict_to_status(_status_to_dict(status))
        assert restored.framework == status.framework
        assert restored.atlas_diff is not None
        assert restored.atlas_diff.new_techniques == ["AML.T0100"]
        assert restored.atlas_diff.deprecated_techniques == ["AML.T0001"]

    def test_round_trip_without_diff(self) -> None:
        status = FrameworkStatus("owasp", "v1", "v2", "up-to-date", "ok")
        restored = _dict_to_status(_status_to_dict(status))
        assert restored.atlas_diff is None
        assert restored.message == "ok"


# ---------------------------------------------------------------------------
# check_frameworks orchestrator with cache
# ---------------------------------------------------------------------------


class TestCheckFrameworksOrchestrator:
    """check_frameworks uses cache on subsequent runs."""

    @patch("q_ai.core.update_frameworks.check_owasp_mcp")
    @patch("q_ai.core.update_frameworks.check_atlas")
    @patch("q_ai.core.update_frameworks._save_cache")
    @patch("q_ai.core.update_frameworks._load_cache")
    @patch("q_ai.core.update_frameworks.load_frameworks_yaml")
    def test_uses_cache_when_available(
        self,
        mock_load_yaml: object,
        mock_load_cache: object,
        mock_save_cache: object,
        mock_atlas: object,
        mock_owasp: object,
    ) -> None:
        mock_load_yaml_fn = mock_load_yaml  # type: ignore[assignment]
        mock_load_yaml_fn.return_value = SAMPLE_FRAMEWORKS
        mock_load_cache_fn = mock_load_cache  # type: ignore[assignment]
        mock_load_cache_fn.return_value = [
            _status_to_dict(FrameworkStatus("mitre_atlas", "v5.4.0", "v5.5.0", "up-to-date")),
        ]

        results = check_frameworks()
        assert len(results) == 1
        assert results[0].framework == "mitre_atlas"

        # Should not have called the actual checkers
        mock_atlas_fn = mock_atlas  # type: ignore[assignment]
        mock_owasp_fn = mock_owasp  # type: ignore[assignment]
        mock_atlas_fn.assert_not_called()
        mock_owasp_fn.assert_not_called()

    @patch("q_ai.core.update_frameworks.check_owasp_mcp")
    @patch("q_ai.core.update_frameworks.check_atlas")
    @patch("q_ai.core.update_frameworks._save_cache")
    @patch("q_ai.core.update_frameworks._load_cache")
    @patch("q_ai.core.update_frameworks.load_frameworks_yaml")
    def test_runs_checks_when_cache_miss(
        self,
        mock_load_yaml: object,
        mock_load_cache: object,
        mock_save_cache: object,
        mock_atlas: object,
        mock_owasp: object,
    ) -> None:
        mock_load_yaml_fn = mock_load_yaml  # type: ignore[assignment]
        mock_load_yaml_fn.return_value = SAMPLE_FRAMEWORKS
        mock_load_cache_fn = mock_load_cache  # type: ignore[assignment]
        mock_load_cache_fn.return_value = None

        mock_atlas_fn = mock_atlas  # type: ignore[assignment]
        mock_atlas_fn.return_value = FrameworkStatus(
            "mitre_atlas", "v5.4.0", "v5.5.0", "update-available"
        )
        mock_owasp_fn = mock_owasp  # type: ignore[assignment]
        mock_owasp_fn.return_value = FrameworkStatus(
            "owasp_mcp_top10", "2025-beta", "2025-beta", "up-to-date"
        )

        results = check_frameworks()
        assert len(results) == 2
        mock_atlas_fn.assert_called_once()
        mock_owasp_fn.assert_called_once()
