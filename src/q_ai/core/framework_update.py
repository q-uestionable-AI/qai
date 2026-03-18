"""Framework update checker for detecting upstream changes.

Checks configured security frameworks against their upstream sources
and reports what has changed. Never modifies frameworks.yaml.

Supported checks:
    MITRE ATLAS — fetches structured ATLAS.yaml from the latest GitHub
    release, diffs technique IDs against local mappings.

    OWASP MCP Top 10 — detects version changes on the OWASP page and
    reports when manual review is needed.

Cache:
    Results are cached in ~/.qai/cache/framework_updates.json with a
    24-hour TTL. Cache is invalidated when reviewed_against or
    last_reviewed fields in frameworks.yaml change.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

ATLAS_RELEASES_URL = "https://api.github.com/repos/mitre-atlas/atlas-data/releases/latest"
OWASP_MCP_URL = "https://owasp.org/www-project-mcp-top-10/"
CACHE_TTL_SECONDS = 86400  # 24 hours
ATLAS_TECHNIQUE_RE = re.compile(r"^AML\.T\d{4}(?:\.\d{3})?$")
CACHE_DIR = Path.home() / ".qai" / "cache"
CACHE_FILE = CACHE_DIR / "framework_updates.json"
HTTP_TIMEOUT = 30


@dataclass
class AtlasCheckResult:
    """Result of an ATLAS upstream check."""

    local_version: str
    upstream_version: str | None = None
    new_techniques: list[str] = field(default_factory=list)
    deprecated_techniques: list[str] = field(default_factory=list)
    error: str | None = None
    from_cache: bool = False


@dataclass
class OwaspMcpCheckResult:
    """Result of an OWASP MCP Top 10 upstream check."""

    local_version: str
    upstream_version: str | None = None
    version_changed: bool = False
    needs_review: bool = False
    error: str | None = None
    from_cache: bool = False


# ---------------------------------------------------------------------------
# Framework metadata helpers
# ---------------------------------------------------------------------------


def load_frameworks_metadata(
    yaml_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load framework metadata from frameworks.yaml.

    Args:
        yaml_path: Optional override path. Defaults to bundled data file.

    Returns:
        Dict of framework name to framework data.
    """
    if yaml_path is None:
        from importlib import resources

        data_files = resources.files("q_ai.core.data")
        resource = data_files.joinpath("frameworks.yaml")
        with resource.open() as f:
            data: dict[str, Any] = yaml.safe_load(f)
    else:
        with yaml_path.open() as f:
            data = yaml.safe_load(f)
    result: dict[str, dict[str, Any]] = data.get("frameworks", {})
    return result


def get_reviewed_version(fw_data: dict[str, Any]) -> str:
    """Get the reviewed-against version, falling back to version field.

    Args:
        fw_data: Single framework entry from frameworks.yaml.

    Returns:
        Version string the local mappings were reviewed against.
    """
    return str(fw_data.get("reviewed_against", fw_data.get("version", "unknown")))


# ---------------------------------------------------------------------------
# ATLAS technique extraction
# ---------------------------------------------------------------------------


def get_local_atlas_techniques(
    frameworks: dict[str, dict[str, Any]],
) -> set[str]:
    """Extract all ATLAS technique IDs from local frameworks.yaml mappings.

    Args:
        frameworks: Parsed frameworks dict from frameworks.yaml.

    Returns:
        Set of ATLAS technique ID strings (e.g. ``AML.T0040``).
    """
    atlas_data = frameworks.get("mitre_atlas", {})
    mappings: dict[str, Any] = atlas_data.get("mappings", {})
    techniques: set[str] = set()
    for value in mappings.values():
        if isinstance(value, str) and ATLAS_TECHNIQUE_RE.match(value):
            techniques.add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and ATLAS_TECHNIQUE_RE.match(item):
                    techniques.add(item)
    return techniques


def extract_atlas_techniques(atlas_yaml: Any) -> set[str]:
    """Extract all technique IDs from structured ATLAS.yaml data.

    Recursively searches for ``id`` keys whose values match the ATLAS
    technique pattern (``AML.Tnnnn`` or ``AML.Tnnnn.nnn``).

    Args:
        atlas_yaml: Parsed ATLAS.yaml content (dict or list).

    Returns:
        Set of ATLAS technique ID strings found upstream.
    """
    techniques: set[str] = set()
    _extract_ids_recursive(atlas_yaml, techniques)
    return techniques


def _extract_ids_recursive(obj: Any, techniques: set[str]) -> None:
    """Walk nested structure collecting technique IDs."""
    if isinstance(obj, dict):
        id_val = obj.get("id")
        if isinstance(id_val, str) and ATLAS_TECHNIQUE_RE.match(id_val):
            techniques.add(id_val)
        for value in obj.values():
            _extract_ids_recursive(value, techniques)
    elif isinstance(obj, list):
        for item in obj:
            _extract_ids_recursive(item, techniques)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch_json(url: str) -> dict[str, Any]:
    """Fetch and parse JSON from a URL.

    Args:
        url: URL to fetch.

    Returns:
        Parsed JSON dict.
    """
    req = Request(  # noqa: S310
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "qai",
        },
    )
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        result: dict[str, Any] = json.loads(resp.read().decode())
        return result


def _fetch_text(url: str) -> str:
    """Fetch raw text from a URL.

    Args:
        url: URL to fetch.

    Returns:
        Response body as string.
    """
    req = Request(url, headers={"User-Agent": "qai"})  # noqa: S310
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        body: str = resp.read().decode()
        return body


def _fetch_yaml(url: str) -> Any:
    """Fetch and parse YAML from a URL.

    Args:
        url: URL to fetch.

    Returns:
        Parsed YAML content.
    """
    text = _fetch_text(url)
    return yaml.safe_load(text)


# ---------------------------------------------------------------------------
# ATLAS check
# ---------------------------------------------------------------------------


def _find_atlas_yaml_asset(release: dict[str, Any]) -> str | None:
    """Find the ATLAS.yaml download URL from a GitHub release.

    Args:
        release: GitHub release API response dict.

    Returns:
        Download URL for ATLAS.yaml, or None if not found.
    """
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if name in ("atlas.yaml", "atlas.yml"):
            return str(asset.get("browser_download_url", ""))
    return None


def check_atlas(
    frameworks: dict[str, dict[str, Any]],
) -> AtlasCheckResult:
    """Check MITRE ATLAS for upstream changes.

    Args:
        frameworks: Parsed frameworks dict from frameworks.yaml.

    Returns:
        AtlasCheckResult with diff information.
    """
    atlas_data = frameworks.get("mitre_atlas", {})
    local_version = get_reviewed_version(atlas_data)
    local_techniques = get_local_atlas_techniques(frameworks)

    release = _fetch_atlas_release()
    if release is None:
        return AtlasCheckResult(
            local_version=local_version,
            error="Failed to fetch ATLAS releases from GitHub",
        )

    upstream_version = release.get("tag_name", "unknown")

    asset_url = _find_atlas_yaml_asset(release)
    if asset_url is None:
        return AtlasCheckResult(
            local_version=local_version,
            upstream_version=upstream_version,
            error="ATLAS.yaml asset not found in release",
        )

    upstream_techniques = _download_atlas_techniques(asset_url)
    if upstream_techniques is None:
        return AtlasCheckResult(
            local_version=local_version,
            upstream_version=upstream_version,
            error="Failed to download or parse ATLAS.yaml",
        )

    new_techniques = sorted(upstream_techniques - local_techniques)
    deprecated = sorted(local_techniques - upstream_techniques)

    return AtlasCheckResult(
        local_version=local_version,
        upstream_version=upstream_version,
        new_techniques=new_techniques,
        deprecated_techniques=deprecated,
    )


def _fetch_atlas_release() -> dict[str, Any] | None:
    """Fetch latest ATLAS release metadata from GitHub."""
    try:
        return _fetch_json(ATLAS_RELEASES_URL)
    except (URLError, json.JSONDecodeError, OSError):
        return None


def _download_atlas_techniques(asset_url: str) -> set[str] | None:
    """Download ATLAS.yaml and extract technique IDs."""
    try:
        atlas_yaml = _fetch_yaml(asset_url)
    except (URLError, yaml.YAMLError, OSError):
        return None
    return extract_atlas_techniques(atlas_yaml)


# ---------------------------------------------------------------------------
# OWASP MCP Top 10 check
# ---------------------------------------------------------------------------

_OWASP_VERSION_PATTERNS = [
    re.compile(r"Version\s*:\s*([\w.\-]+)", re.IGNORECASE),
    re.compile(r"v(\d+\.\d+(?:\.\d+)?(?:-\w+)?)", re.IGNORECASE),
    re.compile(r"(\d{4}-(?:beta|rc\d?|draft))", re.IGNORECASE),
]


def _extract_owasp_version(page_text: str) -> str | None:
    """Extract version indicator from OWASP MCP Top 10 page.

    Args:
        page_text: Raw HTML content of the OWASP page.

    Returns:
        Detected version string, or None if no version marker found.
    """
    for pattern in _OWASP_VERSION_PATTERNS:
        match = pattern.search(page_text)
        if match:
            return match.group(1)
    return None


def check_owasp_mcp(
    frameworks: dict[str, dict[str, Any]],
) -> OwaspMcpCheckResult:
    """Check OWASP MCP Top 10 for version changes.

    Args:
        frameworks: Parsed frameworks dict from frameworks.yaml.

    Returns:
        OwaspMcpCheckResult with version comparison.
    """
    owasp_data = frameworks.get("owasp_mcp_top10", {})
    local_version = get_reviewed_version(owasp_data)

    try:
        page_text = _fetch_text(OWASP_MCP_URL)
    except (URLError, OSError):
        return OwaspMcpCheckResult(
            local_version=local_version,
            error="Failed to fetch OWASP MCP Top 10 page",
        )

    upstream_version = _extract_owasp_version(page_text)
    if upstream_version is None:
        return OwaspMcpCheckResult(
            local_version=local_version,
            needs_review=True,
            error=("Could not detect version on OWASP page — manual review recommended"),
        )

    version_changed = upstream_version != local_version
    return OwaspMcpCheckResult(
        local_version=local_version,
        upstream_version=upstream_version,
        version_changed=version_changed,
        needs_review=version_changed,
    )


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def _compute_frameworks_hash(
    frameworks: dict[str, dict[str, Any]],
) -> str:
    """Compute hash of reviewed_against and last_reviewed fields.

    Args:
        frameworks: Parsed frameworks dict.

    Returns:
        Short hex digest for cache invalidation comparison.
    """
    parts: list[str] = []
    for fw_name in sorted(frameworks.keys()):
        fw_data = frameworks[fw_name]
        reviewed = fw_data.get("reviewed_against", fw_data.get("version", ""))
        last_reviewed = fw_data.get("last_reviewed", "")
        parts.append(f"{fw_name}:{reviewed}:{last_reviewed}")
    content = "|".join(parts)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def load_cache() -> dict[str, Any] | None:
    """Load cached results if they exist.

    Returns:
        Cache dict, or None if unavailable.
    """
    if not CACHE_FILE.exists():
        return None
    try:
        with CACHE_FILE.open() as f:
            loaded: dict[str, Any] = json.load(f)
            return loaded
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(data: dict[str, Any]) -> None:
    """Save results to cache file.

    Args:
        data: Cache data to persist.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w") as f:
        json.dump(data, f, indent=2)


def is_cache_valid(cache: dict[str, Any], frameworks_hash: str) -> bool:
    """Check if cached results are still valid.

    Args:
        cache: Loaded cache dict.
        frameworks_hash: Current frameworks metadata hash.

    Returns:
        True if cache is within TTL and hash matches.
    """
    if cache.get("frameworks_hash") != frameworks_hash:
        return False
    timestamp: float = float(cache.get("timestamp", 0))
    return (time.time() - timestamp) < CACHE_TTL_SECONDS


# ---------------------------------------------------------------------------
# Cache reconstruction helpers
# ---------------------------------------------------------------------------


def _atlas_from_cache(
    cache: dict[str, Any],
    frameworks: dict[str, dict[str, Any]],
) -> AtlasCheckResult:
    """Reconstruct AtlasCheckResult from cached data."""
    atlas_cache = cache.get("atlas", {})
    atlas_data = frameworks.get("mitre_atlas", {})
    return AtlasCheckResult(
        local_version=get_reviewed_version(atlas_data),
        upstream_version=atlas_cache.get("upstream_version"),
        new_techniques=atlas_cache.get("new_techniques", []),
        deprecated_techniques=atlas_cache.get("deprecated_techniques", []),
        error=atlas_cache.get("error"),
        from_cache=True,
    )


def _owasp_from_cache(
    cache: dict[str, Any],
    frameworks: dict[str, dict[str, Any]],
) -> OwaspMcpCheckResult:
    """Reconstruct OwaspMcpCheckResult from cached data."""
    owasp_cache = cache.get("owasp_mcp", {})
    owasp_data = frameworks.get("owasp_mcp_top10", {})
    return OwaspMcpCheckResult(
        local_version=get_reviewed_version(owasp_data),
        upstream_version=owasp_cache.get("upstream_version"),
        version_changed=owasp_cache.get("version_changed", False),
        needs_review=owasp_cache.get("needs_review", False),
        error=owasp_cache.get("error"),
        from_cache=True,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_checks(
    yaml_path: Path | None = None,
    use_cache: bool = True,
) -> tuple[AtlasCheckResult, OwaspMcpCheckResult]:
    """Run all framework update checks, using cache when available.

    Args:
        yaml_path: Optional override for frameworks.yaml location.
        use_cache: Whether to use cached results within TTL.

    Returns:
        Tuple of (AtlasCheckResult, OwaspMcpCheckResult).
    """
    frameworks = load_frameworks_metadata(yaml_path)
    frameworks_hash = _compute_frameworks_hash(frameworks)

    if use_cache:
        cache = load_cache()
        if cache is not None and is_cache_valid(cache, frameworks_hash):
            return (
                _atlas_from_cache(cache, frameworks),
                _owasp_from_cache(cache, frameworks),
            )

    atlas_result = check_atlas(frameworks)
    owasp_result = check_owasp_mcp(frameworks)

    cache_data = {
        "timestamp": time.time(),
        "frameworks_hash": frameworks_hash,
        "atlas": {
            "upstream_version": atlas_result.upstream_version,
            "new_techniques": atlas_result.new_techniques,
            "deprecated_techniques": atlas_result.deprecated_techniques,
            "error": atlas_result.error,
        },
        "owasp_mcp": {
            "upstream_version": owasp_result.upstream_version,
            "version_changed": owasp_result.version_changed,
            "needs_review": owasp_result.needs_review,
            "error": owasp_result.error,
        },
    }
    with contextlib.suppress(OSError):
        save_cache(cache_data)

    return atlas_result, owasp_result
