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
        help="Show resolved absolute paths instead of raw config values.",
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
            # Validate in isolation: do not let the tested file replace the
            # process-global settings used by any later command.
            from arxiv2md_beta.settings import set_settings

            previous = get_settings()
            load_settings(config_path=config_file, force_reload=True)
            set_settings(previous)
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

    from arxiv2md_beta import __version__

    starter_config = f"""# arxiv2md-beta Configuration File
# See documentation for all available options

app:
  environment: development
  log_level: INFO

http:
  fetch_timeout_s: 30.0
  fetch_max_retries: 3
  fetch_backoff_s: 1.0
  user_agent: "arxiv2md-beta/{__version__}"
  retry_status_codes: [429, 500, 502, 503, 504]
  large_transfer_timeout_multiplier: 3.0
  max_connections: 100
  max_keepalive_connections: 20

cache:
  dir: "~/.cache/arxiv2md-beta"
  ttl_seconds: 86400

cli_defaults:
  parser: html
  source: Arxiv
  section_filter_mode: exclude
  output_dir: "."
  images_subdir: "images"

images:
  pdf_to_png_dpi: 200
  trim_whitespace: false
  trim_whitespace_tolerance: 100
  disable_tqdm: false

output:
  tiktoken_encoding: "o200k_base"
"""

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
