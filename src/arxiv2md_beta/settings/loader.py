"""Load and merge YAML configuration with env and CLI overrides."""

from __future__ import annotations

import json
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


_ENV_PREFIX = "ARXIV2MD_BETA_"


def _parse_env_scalar(raw: str) -> Any:
    """Coerce env string to bool / int / float / JSON / str."""
    v = raw.strip()
    if v == "":
        return None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v[0] in "[{":
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        pass
    return raw


def _set_nested(target: dict[str, Any], keys: list[str], value: Any) -> None:
    """Set target[k1][k2]...[kn] = value (mutates target)."""
    cur: Any = target
    for k in keys[:-1]:
        nxt = cur.setdefault(k, {})
        if not isinstance(nxt, dict):
            raise ConfigurationError(
                f"Configuration env key conflicts with non-mapping at {k!r}",
                hint="Check ARXIV2MD_BETA_* nested keys for duplicate prefixes.",
            )
        cur = nxt
    cur[keys[-1]] = value


def env_overlay_from_os() -> dict[str, Any]:
    """Build nested dict from ARXIV2MD_BETA_* env vars (same nesting as former pydantic-settings)."""
    out: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        rest = key[len(_ENV_PREFIX) :]
        if rest.startswith("_"):
            rest = rest[1:]
        if not rest:
            continue
        parts = [p.lower() for p in rest.split("__")]
        if not parts:
            continue
        val = _parse_env_scalar(raw)
        if val is None:
            continue
        _set_nested(out, parts, val)
    return out


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

    # Env wins over all YAML (init kwargs previously blocked pydantic-settings env)
    merged = deep_merge(merged, env_overlay_from_os())

    try:
        settings = AppSettings.model_validate(merged)
    except ValidationError as e:
        raise ConfigurationError(
            f"Invalid configuration: {e}",
            hint=(
                "Fix keys in your YAML or set env vars, e.g. "
                "export ARXIV2MD_BETA_HTTP__FETCH_TIMEOUT_S=15"
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
