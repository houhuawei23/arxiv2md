"""Load and merge YAML configuration with env and CLI overrides."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from arxiv2md_beta.settings.schema import AppSettings

_SETTINGS: AppSettings | None = None
_LAST_LOAD_KEY: tuple[Any, ...] | None = None


class ConfigurationError(Exception):
    """Invalid or missing configuration."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.hint = hint
        full = message
        if hint:
            full = f"{message}\n\n{hint}"
        super().__init__(full)


def deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge dict b into a copy of a (b wins on conflicts)."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml_bytes(data: bytes) -> dict[str, Any]:
    loaded = yaml.safe_load(data)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigurationError(
            "Configuration root must be a YAML mapping (object).",
            hint="Fix the YAML file to use key: value at the top level.",
        )
    return loaded


def _read_resource(package: str, resource: str) -> bytes:
    try:
        files = resources.files(package)
        path = files.joinpath(resource)
        with path.open("rb") as f:
            return f.read()
    except FileNotFoundError as e:
        raise ConfigurationError(
            f"Bundled configuration resource not found: {package}/{resource}",
            hint="Reinstall the package or check that package-data includes config/*.yml.",
        ) from e


def _try_read_resource(package: str, resource: str) -> bytes | None:
    try:
        files = resources.files(package)
        path = files.joinpath(resource)
        if not path.is_file():
            return None
        with path.open("rb") as f:
            return f.read()
    except (FileNotFoundError, OSError, TypeError):
        return None


def _read_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {path}",
            hint="Set ARXIV2MD_BETA_CONFIG_PATH to a valid YAML file or pass --config.",
        )
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ConfigurationError(f"Cannot read configuration file {path}: {e}") from e
    try:
        return _load_yaml_bytes(raw)
    except yaml.YAMLError as e:
        raise ConfigurationError(
            f"Invalid YAML in {path}: {e}",
            hint="Validate the file with a YAML linter or fix syntax errors.",
        ) from e


def load_settings(
    *,
    config_path: Path | None = None,
    environment: str | None = None,
    force_reload: bool = False,
) -> AppSettings:
    """Load settings from bundled defaults, environment profile, optional user file, then env vars.

    Merge order (low to high): default_config.yml < environments/<env>.yml < user YAML < process env.

    Parameters
    ----------
    config_path
        Optional user YAML path (also from ARXIV2MD_BETA_CONFIG_PATH).
    environment
        Profile name (development/production/test). Overrides ARXIV2MD_BETA_APP__ENVIRONMENT.
    force_reload
        Ignore cached settings and rebuild from disk/env.
    """
    global _SETTINGS, _LAST_LOAD_KEY

    user_path = config_path
    if user_path is None:
        env_p = os.getenv("ARXIV2MD_BETA_CONFIG_PATH")
        if env_p:
            user_path = Path(env_p).expanduser()

    key = (user_path, environment, os.environ.get("ARXIV2MD_BETA_APP__ENVIRONMENT"))
    if not force_reload and _SETTINGS is not None and key == _LAST_LOAD_KEY:
        return _SETTINGS

    try:
        base_raw = _read_resource("arxiv2md_beta.config", "default_config.yml")
        merged = _load_yaml_bytes(base_raw)
    except ConfigurationError:
        raise
    except Exception as e:
        raise ConfigurationError(
            f"Failed to load bundled default_config.yml: {e}",
            hint="Ensure arxiv2md_beta is installed with package data (see pyproject.toml).",
        ) from e

    env_name = (
        environment
        or os.getenv("ARXIV2MD_BETA_APP__ENVIRONMENT")
        or (merged.get("app") or {}).get("environment")
        or "development"
    )

    prof_raw = _try_read_resource("arxiv2md_beta.config", f"environments/{env_name}.yml")
    if prof_raw is not None:
        merged = deep_merge(merged, _load_yaml_bytes(prof_raw))

    if user_path is not None:
        merged = deep_merge(merged, _read_path(user_path))

    try:
        settings = AppSettings(**merged)
    except ValidationError as e:
        raise ConfigurationError(
            f"Invalid configuration: {e}",
            hint=(
                "Fix keys in your YAML or set env vars, e.g. "
                "export ARXIV2MD_BETA__HTTP__FETCH_TIMEOUT_S=15"
            ),
        ) from e

    _SETTINGS = settings
    _LAST_LOAD_KEY = key
    return settings


def get_settings() -> AppSettings:
    """Return loaded settings, loading defaults from bundled YAML if needed."""
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load_settings()
    return _SETTINGS


def reset_settings_cache() -> None:
    """Clear cached settings (mainly for tests)."""
    global _SETTINGS, _LAST_LOAD_KEY
    _SETTINGS = None
    _LAST_LOAD_KEY = None


def set_settings(settings: AppSettings) -> None:
    """Replace the global settings instance (e.g. after CLI overrides)."""
    global _SETTINGS, _LAST_LOAD_KEY
    _SETTINGS = settings
    _LAST_LOAD_KEY = None


def apply_cli_overrides(settings: AppSettings, args: Any) -> AppSettings:
    """Apply argparse Namespace onto settings (CLI wins over file/env)."""
    cli = settings.cli_defaults.model_copy()
    if getattr(args, "parser", None) is not None:
        cli = cli.model_copy(update={"parser": args.parser})
    if getattr(args, "source", None) is not None:
        cli = cli.model_copy(update={"source": args.source})
    if getattr(args, "section_filter_mode", None) is not None:
        cli = cli.model_copy(update={"section_filter_mode": args.section_filter_mode})
    out = settings.model_copy(update={"cli_defaults": cli})
    return out
