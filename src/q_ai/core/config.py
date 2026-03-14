"""Configuration management for q-ai.

Supports a hybrid approach:
- YAML file (~/.qai/config.yaml) for credentials and static config
- SQLite settings table for operational defaults
- Precedence resolver: CLI value > env var > DB setting > config file > default
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path.home() / ".qai" / "config.yaml"


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file. Defaults to ~/.qai/config.yaml.

    Returns:
        Configuration dict. Empty dict if file doesn't exist.
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}


def get_credential(
    provider: str,
    config_path: Path | None = None,
) -> str | None:
    """Get an API key for a provider from the config file.

    Args:
        provider: Provider name (e.g. "anthropic", "openai").
        config_path: Optional override for config file path.

    Returns:
        API key string or None if not found.
    """
    config = load_config(config_path)
    providers = config.get("providers", {})
    provider_config = providers.get(provider, {})
    return provider_config.get("api_key")


def set_credential(
    provider: str,
    api_key: str,
    config_path: Path | None = None,
) -> None:
    """Write or update a provider API key in the config file.

    Creates the config file and parent directory if needed.
    Sets restrictive permissions (0o600) on the file (Unix only).

    Args:
        provider: Provider name.
        api_key: The API key to store.
        config_path: Optional override for config file path.
    """
    path = config_path or _DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    config = load_config(path)
    if "providers" not in config:
        config["providers"] = {}
    config["providers"][provider] = {"api_key": api_key}
    with open(path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False)
    # Set restrictive permissions on non-Windows
    if sys.platform != "win32":
        path.chmod(0o600)


def get_lab_setting(
    key: str,
    config_path: Path | None = None,
) -> str | None:
    """Read a setting from the config file's lab section.

    Args:
        key: Setting key within the lab section.
        config_path: Optional override for config file path.

    Returns:
        Setting value or None if not found.
    """
    config = load_config(config_path)
    lab = config.get("lab", {})
    return lab.get(key)


def resolve(
    key: str,
    cli_value: str | None = None,
    env_var: str | None = None,
    db_path: Path | None = None,
    config_path: Path | None = None,
) -> tuple[str | None, str]:
    """Resolve a configuration value using the precedence chain.

    Precedence:
        CLI value > environment variable > DB setting >
        config file > None.

    Args:
        key: The setting key to look up.
        cli_value: Optional value from CLI argument.
        env_var: Optional environment variable name to check.
        db_path: Optional override for database path.
        config_path: Optional override for config file path.

    Returns:
        Tuple of (value, source) where source is one of
        "cli", "env", "db", "file", or "default".
    """
    if cli_value is not None:
        return cli_value, "cli"

    if env_var is not None:
        env_value = os.environ.get(env_var)
        if env_value is not None:
            return env_value, "env"

    # Check DB settings
    from q_ai.core.db import get_connection, get_setting

    try:
        with get_connection(db_path) as conn:
            db_value = get_setting(conn, key)
            if db_value is not None:
                return db_value, "db"
    except Exception:
        pass

    # Check config file
    config = load_config(config_path)
    # Walk nested keys
    # e.g. "audit.default_transport" ->
    #      config["audit"]["default_transport"]
    parts = key.split(".")
    current: object = config
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            current = None
            break
    if current is not None and not isinstance(current, dict):
        return str(current), "file"

    return None, "default"
