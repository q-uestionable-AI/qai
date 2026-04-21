"""Check configured frameworks for upstream changes.

Fetches the latest MITRE ATLAS release and OWASP MCP Top 10 page, compares
against the local ``frameworks.yaml`` state, and reports what has changed.
Never modifies ``frameworks.yaml`` — all output is informational.

This is the direct counterpart to the planned ``qai update-cves`` command
documented in ``audit/scanner/supply_chain.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ATLAS_RELEASES_URL = "https://api.github.com/repos/mitre-atlas/atlas-data/releases/latest"
OWASP_MCP_URL = "https://owasp.org/www-project-mcp-top-10/"
CACHE_TTL = timedelta(hours=24)
CACHE_DIR_NAME = "cache"
CACHE_FILE_NAME = "framework_updates.json"
REQUEST_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AtlasDiff:
    """Detailed diff between local and upstream ATLAS techniques."""

    new_techniques: list[str] = field(default_factory=list)
    deprecated_techniques: list[str] = field(default_factory=list)


@dataclass
class FrameworkStatus:
    """Status of a single framework check."""

    framework: str
    local_version: str
    upstream_version: str
    status: str  # "up-to-date", "update-available", "error", "check-skipped"
    message: str = ""
    atlas_diff: AtlasDiff | None = None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _get_cache_path() -> Path:
    """Return path to the framework update cache file."""
    return Path.home() / ".qai" / CACHE_DIR_NAME / CACHE_FILE_NAME


def _compute_invalidation_key(frameworks: dict) -> str:
    """Hash ``reviewed_against`` and ``last_reviewed`` fields for cache invalidation.

    Args:
        frameworks: The ``frameworks`` dict from ``frameworks.yaml``.

    Returns:
        Hex digest string that changes when tracked fields change.
    """
    parts: list[str] = []
    for name in sorted(frameworks.keys()):
        fw = frameworks[name]
        parts.append(f"{name}:{fw.get('reviewed_against', '')}:{fw.get('last_reviewed', '')}")
        parts.append(f"{name}:version:{fw.get('version', '')}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _is_cache_fresh(data: dict) -> bool:
    """Check whether a parsed cache payload is still within the TTL.

    Args:
        data: Parsed JSON cache dict.

    Returns:
        ``True`` if the ``cached_at`` timestamp is valid, timezone-aware,
        and within ``CACHE_TTL``. ``False`` otherwise.
    """
    try:
        cached_at = datetime.fromisoformat(data["cached_at"])
    except (ValueError, KeyError):
        return False

    if cached_at.tzinfo is None:
        return False

    try:
        return datetime.now(tz=UTC) - cached_at <= CACHE_TTL
    except TypeError:
        return False


def _load_cache(invalidation_key: str) -> list[dict] | None:
    """Load cached results if valid and within TTL.

    Args:
        invalidation_key: Current invalidation key to compare against.

    Returns:
        List of serialised ``FrameworkStatus`` dicts, or ``None`` if cache
        is missing, expired, or invalidated.
    """
    cache_path = _get_cache_path()
    if not cache_path.exists():
        return None

    try:
        with cache_path.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if (
        not isinstance(data, dict)
        or data.get("invalidation_key") != invalidation_key
        or not _is_cache_fresh(data)
    ):
        return None

    results = data.get("results")
    return results if isinstance(results, list) else None


def _save_cache(results: list[dict], invalidation_key: str) -> None:
    """Save results to the cache file.

    Args:
        results: List of serialised ``FrameworkStatus`` dicts.
        invalidation_key: Current invalidation key.
    """
    from q_ai.core.paths import ensure_qai_dir

    cache_path = _get_cache_path()
    ensure_qai_dir(cache_path.parent.parent)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at": datetime.now(tz=UTC).isoformat(),
        "invalidation_key": invalidation_key,
        "results": results,
    }
    with cache_path.open("w") as f:
        json.dump(payload, f, indent=2)


def _status_to_dict(status: FrameworkStatus) -> dict:
    """Serialise a ``FrameworkStatus`` to a JSON-safe dict."""
    d: dict = {
        "framework": status.framework,
        "local_version": status.local_version,
        "upstream_version": status.upstream_version,
        "status": status.status,
        "message": status.message,
    }
    if status.atlas_diff is not None:
        d["atlas_diff"] = {
            "new_techniques": status.atlas_diff.new_techniques,
            "deprecated_techniques": status.atlas_diff.deprecated_techniques,
        }
    return d


def _dict_to_status(d: dict) -> FrameworkStatus:
    """Deserialise a dict back into a ``FrameworkStatus``."""
    diff_data = d.get("atlas_diff")
    atlas_diff = None
    if diff_data is not None:
        atlas_diff = AtlasDiff(
            new_techniques=diff_data.get("new_techniques", []),
            deprecated_techniques=diff_data.get("deprecated_techniques", []),
        )
    return FrameworkStatus(
        framework=d["framework"],
        local_version=d["local_version"],
        upstream_version=d["upstream_version"],
        status=d["status"],
        message=d.get("message", ""),
        atlas_diff=atlas_diff,
    )


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http_get(url: str, *, accept: str = "application/json") -> bytes:
    """Perform an HTTP GET request and return the response body.

    Args:
        url: URL to fetch (must be ``https://``).
        accept: Value for the ``Accept`` header.

    Returns:
        Raw response bytes.

    Raises:
        urllib.error.URLError: On network or HTTP errors.
        ValueError: If the URL scheme is not HTTPS.
    """
    if not url.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are allowed, got: {url}")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "Accept": accept,
            "User-Agent": "qai-framework-checker/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=ctx) as resp:  # noqa: S310
        body: bytes = resp.read()
        return body


# ---------------------------------------------------------------------------
# Framework YAML loading
# ---------------------------------------------------------------------------


def load_frameworks_yaml(yaml_path: Path | None = None) -> dict:
    """Load the ``frameworks`` dict from ``frameworks.yaml``.

    Args:
        yaml_path: Optional explicit path. Defaults to bundled data file.

    Returns:
        The ``frameworks`` dict from the YAML.
    """
    if yaml_path is None:
        from importlib import resources

        data_files = resources.files("q_ai.core.data")
        resource = data_files.joinpath("frameworks.yaml")
        with resource.open() as f:
            data: dict = yaml.safe_load(f)
    else:
        with yaml_path.open() as f:
            data = yaml.safe_load(f)
    frameworks: dict = data.get("frameworks", {})
    return frameworks


# ---------------------------------------------------------------------------
# ATLAS check
# ---------------------------------------------------------------------------


def _extract_local_atlas_ids(frameworks: dict) -> set[str]:
    """Collect all ATLAS technique IDs from the local mappings.

    Args:
        frameworks: The ``frameworks`` dict from ``frameworks.yaml``.

    Returns:
        Set of technique ID strings (e.g. ``{"AML.T0043", "AML.T0051.000"}``).
    """
    atlas_data = frameworks.get("mitre_atlas", {})
    mappings = atlas_data.get("mappings", {})
    ids: set[str] = set()
    for value in mappings.values():
        if isinstance(value, list):
            ids.update(value)
        elif isinstance(value, str):
            ids.add(value)
    return ids


def _extract_upstream_atlas_ids(atlas_yaml_bytes: bytes) -> set[str]:
    """Parse the upstream ``ATLAS.yaml`` and extract technique IDs.

    Handles the standard MITRE ATLAS data format where techniques are listed
    under a top-level key with ``id`` fields matching ``AML.Tnnnn`` patterns.

    Args:
        atlas_yaml_bytes: Raw bytes of the downloaded ATLAS.yaml file.

    Returns:
        Set of technique ID strings found in the upstream data.
    """
    data = yaml.safe_load(atlas_yaml_bytes)
    ids: set[str] = set()
    atlas_id_pattern = re.compile(r"^AML\.T\d{4}(?:\.\d{3})?$")

    def _walk(obj: object) -> None:
        if isinstance(obj, dict):
            tid = obj.get("id", "")
            if isinstance(tid, str) and atlas_id_pattern.match(tid):
                ids.add(tid)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return ids


def _find_atlas_asset_url(release_data: dict) -> str | None:
    """Discover the ATLAS.yaml download URL from a GitHub release response.

    Args:
        release_data: Parsed JSON from the GitHub releases API.

    Returns:
        Browser download URL for the ATLAS YAML asset, or ``None``.
    """
    for asset in release_data.get("assets", []):
        name = asset.get("name", "").lower()
        if "atlas" in name and name.endswith((".yaml", ".yml")):
            url: str | None = asset.get("browser_download_url")
            return url
    return None


def _build_atlas_diff_status(
    local_version: str,
    upstream_version: str,
    upstream_ids: set[str],
    local_ids: set[str],
) -> FrameworkStatus:
    """Compare local and upstream ATLAS technique IDs and build a status.

    Args:
        local_version: The ``reviewed_against`` value from ``frameworks.yaml``.
        upstream_version: The tag name from the latest GitHub release.
        upstream_ids: Technique IDs extracted from the upstream ATLAS.yaml.
        local_ids: Technique IDs extracted from the local mappings.

    Returns:
        A ``FrameworkStatus`` reflecting whether an update is available.
    """
    new_techniques = sorted(upstream_ids - local_ids)
    deprecated_techniques = sorted(local_ids - upstream_ids)

    # Normalise versions for comparison (strip leading 'v' if present)
    local_norm = local_version.lstrip("v").strip()
    upstream_norm = upstream_version.lstrip("v").strip()

    if local_norm == upstream_norm and not new_techniques and not deprecated_techniques:
        return FrameworkStatus(
            framework="mitre_atlas",
            local_version=local_version,
            upstream_version=upstream_version,
            status="up-to-date",
            message="Local mappings match upstream",
        )

    diff = AtlasDiff(
        new_techniques=new_techniques,
        deprecated_techniques=deprecated_techniques,
    )
    parts: list[str] = []
    if local_norm != upstream_norm:
        parts.append(f"Version delta: {local_version} -> {upstream_version}")
    if new_techniques:
        parts.append(f"{len(new_techniques)} new technique(s) not yet mapped")
    if deprecated_techniques:
        parts.append(f"{len(deprecated_techniques)} mapped technique(s) no longer upstream")

    return FrameworkStatus(
        framework="mitre_atlas",
        local_version=local_version,
        upstream_version=upstream_version,
        status="update-available",
        message="; ".join(parts),
        atlas_diff=diff,
    )


def check_atlas(frameworks: dict) -> FrameworkStatus:
    """Check MITRE ATLAS for upstream changes.

    Args:
        frameworks: The ``frameworks`` dict from ``frameworks.yaml``.

    Returns:
        A ``FrameworkStatus`` with diff details.
    """
    atlas_data = frameworks.get("mitre_atlas", {})
    local_version = atlas_data.get("reviewed_against", "unknown")

    try:
        release_bytes = _http_get(ATLAS_RELEASES_URL)
        release_data = json.loads(release_bytes)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        return FrameworkStatus(
            framework="mitre_atlas",
            local_version=local_version,
            upstream_version="unknown",
            status="error",
            message=f"Failed to fetch ATLAS release info: {exc}",
        )

    upstream_version = release_data.get("tag_name", "unknown")

    asset_url = _find_atlas_asset_url(release_data)
    if asset_url is None:
        return FrameworkStatus(
            framework="mitre_atlas",
            local_version=local_version,
            upstream_version=upstream_version,
            status="error",
            message="ATLAS.yaml asset not found in latest release",
        )

    try:
        atlas_yaml_bytes = _http_get(asset_url, accept="application/octet-stream")
    except (urllib.error.URLError, OSError) as exc:
        return FrameworkStatus(
            framework="mitre_atlas",
            local_version=local_version,
            upstream_version=upstream_version,
            status="error",
            message=f"Failed to download ATLAS.yaml: {exc}",
        )

    try:
        upstream_ids = _extract_upstream_atlas_ids(atlas_yaml_bytes)
    except yaml.YAMLError as exc:
        return FrameworkStatus(
            framework="mitre_atlas",
            local_version=local_version,
            upstream_version=upstream_version,
            status="error",
            message=f"Failed to parse ATLAS.yaml: {exc}",
        )

    local_ids = _extract_local_atlas_ids(frameworks)
    return _build_atlas_diff_status(local_version, upstream_version, upstream_ids, local_ids)


# ---------------------------------------------------------------------------
# OWASP MCP Top 10 check
# ---------------------------------------------------------------------------


def check_owasp_mcp(frameworks: dict) -> FrameworkStatus:
    """Check OWASP MCP Top 10 page for version changes.

    Args:
        frameworks: The ``frameworks`` dict from ``frameworks.yaml``.

    Returns:
        A ``FrameworkStatus`` indicating whether manual review is needed.
    """
    owasp_data = frameworks.get("owasp_mcp_top10", {})
    local_version = owasp_data.get("version", "unknown")

    try:
        page_bytes = _http_get(OWASP_MCP_URL, accept="text/html")
        page_text = page_bytes.decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        return FrameworkStatus(
            framework="owasp_mcp_top10",
            local_version=local_version,
            upstream_version="unknown",
            status="error",
            message=f"Failed to fetch OWASP MCP Top 10 page: {exc}",
        )

    upstream_version = _detect_owasp_version(page_text)
    if upstream_version is None:
        return FrameworkStatus(
            framework="owasp_mcp_top10",
            local_version=local_version,
            upstream_version="unknown",
            status="update-available",
            message="Could not detect version marker; manual review recommended",
        )

    if upstream_version == local_version:
        return FrameworkStatus(
            framework="owasp_mcp_top10",
            local_version=local_version,
            upstream_version=upstream_version,
            status="up-to-date",
            message="Version unchanged",
        )

    return FrameworkStatus(
        framework="owasp_mcp_top10",
        local_version=local_version,
        upstream_version=upstream_version,
        status="update-available",
        message=f"Version changed ({local_version} -> {upstream_version}); manual review needed",
    )


def _detect_owasp_version(page_text: str) -> str | None:
    """Attempt to extract a version indicator from the OWASP MCP Top 10 page.

    Looks for common patterns like ``Version: X.Y``, ``v1.0``, or year-based
    indicators like ``2025-beta``.

    Args:
        page_text: HTML page content as string.

    Returns:
        Detected version string, or ``None`` if no marker found.
    """
    patterns = [
        r"(?i)version[:\s]+([0-9]{4}[-\w.]*)",
        r"(?i)version[:\s]+(v?[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-\w.]*))",
        r"(?i)(?:MCP\s+Top\s+10\s+)(v?[0-9]{4}[-\w.]*)",
        r"(?i)(?:MCP\s+Top\s+10\s+)(v?[0-9]+\.[0-9]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def check_frameworks(
    yaml_path: Path | None = None,
    *,
    skip_cache: bool = False,
) -> list[FrameworkStatus]:
    """Run all framework checks, using cache when available.

    Args:
        yaml_path: Optional explicit path to ``frameworks.yaml``.
        skip_cache: If ``True``, bypass the cache entirely.

    Returns:
        List of ``FrameworkStatus`` results for each checked framework.
    """
    frameworks = load_frameworks_yaml(yaml_path)
    invalidation_key = _compute_invalidation_key(frameworks)

    if not skip_cache:
        cached = _load_cache(invalidation_key)
        if cached is not None:
            return [_dict_to_status(d) for d in cached]

    results: list[FrameworkStatus] = []
    results.append(check_atlas(frameworks))
    results.append(check_owasp_mcp(frameworks))

    has_errors = any(r.status == "error" for r in results)
    if not skip_cache and not has_errors:
        serialised = [_status_to_dict(r) for r in results]
        _save_cache(serialised, invalidation_key)

    return results
