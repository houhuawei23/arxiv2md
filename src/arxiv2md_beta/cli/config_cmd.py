"""Config CLI subcommand for arxiv2md-beta.

Provides commands for viewing, validating, and managing configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from arxiv2md_beta.settings import get_settings, load_settings
from arxiv2md_beta.utils.logging_config import get_logger

app = typer.Typer(name="config", help="Configuration management commands.")
console = Console()
logger = get_logger()


@app.command("show")
def config_show(
    format: str = typer.Option(
        "yaml",
        "--format",
        "-f",
        help="Output format: yaml, json, or table",
    ),
    resolve_paths: bool = typer.Option(
        True,
        "--resolve-paths/--no-resolve-paths",
        help="Resolve cache.dir to its effective absolute path (the only path with env/XDG resolution).",
    ),
) -> None:
    """Display the effective configuration."""
    settings = get_settings()

    # Full model dump: always in sync with the schema (a hand-enumerated
    # dict drifted and hid e.g. the active output_naming.naming_scheme).
    config_dict: dict[str, Any] = settings.model_dump()
    if resolve_paths:
        config_dict.setdefault("cache", {})["dir"] = str(settings.resolved_cache_path())

    if format == "yaml":
        yaml_str = yaml.dump(config_dict, default_flow_style=False, sort_keys=True)
        syntax = Syntax(yaml_str, "yaml", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title="Effective Configuration"))
    elif format == "json":
        console.print(json.dumps(config_dict, indent=2, default=str))
    elif format == "table":
        table = Table(title="Effective Configuration")
        table.add_column("Section", style="cyan")
        table.add_column("Key", style="magenta")
        table.add_column("Value", style="green")

        for section, values in config_dict.items():
            for key, value in values.items():
                table.add_row(section, key, str(value))
        console.print(table)
    else:
        typer.echo(f"Error: Unknown format '{format}'. Use yaml, json, or table.", err=True)
        raise typer.Exit(code=2)


def _validate_config_file(config_file: Path) -> None:
    """Load *config_file* in isolation and restore the previous globals.

    A broken candidate file must not leave its half-loaded state as the
    process-global settings — that polluted every later command in the same
    process (audit5 R-8).
    """
    from arxiv2md_beta.settings import set_settings

    previous = get_settings()
    try:
        load_settings(config_path=config_file, force_reload=True)
    finally:
        set_settings(previous)


@app.command("validate")
def config_validate(
    config_file: Path | None = typer.Argument(
        None,
        help="Path to config file to validate (default: current effective config)",
    ),
) -> None:
    """Validate a configuration file."""
    try:
        if config_file:
            _validate_config_file(config_file)
            console.print(f"[green]✓[/green] Configuration file is valid: {config_file}")
        else:
            # Just re-validate current settings
            _ = get_settings()
            console.print("[green]✓[/green] Current configuration is valid")
    except Exception as e:
        console.print(f"[red]✗[/red] Configuration error: {e}")
        raise typer.Exit(code=2) from e


@app.command("init")
def config_init(
    output: Path = typer.Option(
        Path.home() / ".config" / "arxiv2md-beta" / "config.yml",
        "--output",
        "-o",
        help="Path for the new configuration file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing file.",
    ),
) -> None:
    """Create a starter configuration file."""
    if output.exists() and not force:
        console.print(f"[red]✗[/red] File already exists: {output}")
        console.print("Use --force to overwrite.")
        raise typer.Exit(code=2)

    # Emit the validated default bundle instead of a hand-written template:
    # the hand-written one drifted (dpi 200 vs 150, backoff 1.0 vs 3.0,
    # missing sections) and hardcoded the version into user_agent, defeating
    # the settings-layer placeholder injection on later upgrades (audit5
    # G4-6). The bundle is the single source of defaults, keeps its
    # explanatory comments, and carries the "arxiv2md-beta" placeholder
    # rather than a frozen version string.
    from arxiv2md_beta.settings.loader import _load_yaml_bytes, _read_resource
    from arxiv2md_beta.settings.schema import AppSettings

    raw = _read_resource("arxiv2md_beta.config", "default_config.yml")
    AppSettings.model_validate(_load_yaml_bytes(raw))  # refuse to emit an invalid starter
    starter_config = raw.decode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(starter_config, encoding="utf-8")
    console.print(f"[green]✓[/green] Created configuration file: {output}")


@app.command("get")
def config_get(
    key: str = typer.Argument(
        ...,
        help="Configuration key in dot notation (e.g., 'http.fetch_timeout_s').",
    ),
) -> None:
    """Get a specific configuration value."""
    settings = get_settings()

    # Navigate the settings object using dot notation
    parts = key.split(".")
    value = settings
    for part in parts:
        if hasattr(value, part):
            value = getattr(value, part)
        else:
            console.print(f"[red]✗[/red] Unknown configuration key: {key}")
            raise typer.Exit(code=2)

    console.print(str(value))
