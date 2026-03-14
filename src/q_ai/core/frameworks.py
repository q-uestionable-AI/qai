"""Framework resolver for mapping categories to security framework IDs."""
from __future__ import annotations

from pathlib import Path

import yaml


class FrameworkResolver:
    """Loads framework mapping data and resolves category to framework IDs.

    Uses importlib.resources to load the bundled YAML data file so it works
    from installed packages, not just source checkouts.
    """

    def __init__(self, yaml_path: Path | None = None) -> None:
        """Load framework mappings from YAML.

        Args:
            yaml_path: Path to YAML file. Defaults to bundled data file.
        """
        if yaml_path is None:
            import importlib.resources as resources

            data_files = resources.files("q_ai.core.data")
            yaml_path = data_files.joinpath("frameworks.yaml")
        with open(str(yaml_path)) as f:
            data = yaml.safe_load(f)
        self._frameworks: dict = data.get("frameworks", {})

    def resolve(self, category: str) -> dict[str, str | list[str]]:
        """Return all framework IDs for a category.

        Args:
            category: Internal category string (e.g. "command_injection").

        Returns:
            Dict mapping framework name to ID(s). Empty dict if category unknown.
        """
        result: dict[str, str | list[str]] = {}
        for fw_name, fw_data in self._frameworks.items():
            mappings = fw_data.get("mappings", {})
            if category in mappings:
                result[fw_name] = mappings[category]
        return result

    def resolve_one(
        self,
        category: str,
        framework: str,
    ) -> str | list[str] | None:
        """Return a single framework's ID(s) for a category.

        Args:
            category: Internal category string.
            framework: Framework name (e.g. "owasp_mcp_top10").

        Returns:
            Framework ID string, list of IDs, or None if not mapped.
        """
        fw_data = self._frameworks.get(framework)
        if fw_data is None:
            return None
        return fw_data.get("mappings", {}).get(category)

    def list_frameworks(self) -> list[str]:
        """Return available framework names."""
        return list(self._frameworks.keys())

    def list_categories(self) -> list[str]:
        """Return all known categories across all frameworks."""
        categories: set[str] = set()
        for fw_data in self._frameworks.values():
            categories.update(fw_data.get("mappings", {}).keys())
        return sorted(categories)
