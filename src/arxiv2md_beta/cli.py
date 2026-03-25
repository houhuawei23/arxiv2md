"""Command-line interface for arxiv2md-beta."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arxiv2md_beta.settings import (
    apply_cli_overrides,
    get_settings,
    load_settings,
    set_settings,
)
from arxiv2md_beta.settings.schema import AppSettings


def parse_preliminary_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse --config / --env / --force-reload before full settings merge."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="User YAML configuration file (merged after bundled default and environment profile).",
    )
    p.add_argument(
        "--env",
        "-E",
        default=None,
        metavar="NAME",
        help="Environment profile: development, production, or test (selects environments/<NAME>.yml).",
    )
    p.add_argument(
        "--force-reload",
        action="store_true",
        help="Reload configuration from disk instead of using cached settings.",
    )
    return p.parse_known_args(argv)[0]


def build_main_parser(settings: AppSettings) -> argparse.ArgumentParser:
    """Build the main CLI parser with defaults taken from settings."""
    d = settings.cli_defaults
    parser = argparse.ArgumentParser(
        prog="arxiv2md-beta",
        description="Convert arXiv papers to Markdown with image support. Supports both HTML and LaTeX parsing modes.",
    )
    parser.add_argument(
        "input_text",
        help="arXiv ID, URL, or local archive path (e.g., 2501.11120v1, https://arxiv.org/abs/2501.11120, /path/to/paper.tar.gz, /path/to/paper.zip)",
    )
    parser.add_argument(
        "--parser",
        choices=("html", "latex"),
        default=d.parser,
        help="Parser mode: html or latex",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory path. A subdirectory will be created with format '[date]-[source]-[short]-[title]'",
    )
    parser.add_argument(
        "--source",
        default=d.source,
        help="Article source (conference/journal name).",
    )
    parser.add_argument(
        "--short",
        default=None,
        help="Short name for the article (e.g., Dreamer3)",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip downloading and inserting images (HTML mode only)",
    )
    parser.add_argument(
        "--remove-refs",
        action="store_true",
        help="Remove bibliography/references sections from output.",
    )
    parser.add_argument(
        "--remove-toc",
        action="store_true",
        help="Remove table of contents from output.",
    )
    parser.add_argument(
        "--remove-inline-citations",
        action="store_true",
        help="Remove inline citation text (e.g., '(Smith et al., 2023)') from output.",
    )
    parser.add_argument(
        "--section-filter-mode",
        choices=("include", "exclude"),
        default=d.section_filter_mode,
        help="Section filtering mode when using --sections/--section.",
    )
    parser.add_argument(
        "--sections",
        default=None,
        help='Comma-separated section titles (e.g., "Abstract,Introduction").',
    )
    parser.add_argument(
        "--section",
        action="append",
        help="Repeatable section title filter (can be used multiple times).",
    )
    parser.add_argument(
        "--include-tree",
        action="store_true",
        help="Include the section tree before the Markdown content.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv: preliminary flags, load settings, then main parser."""
    if argv is None:
        argv = sys.argv[1:]
    pre = parse_preliminary_args(argv)
    load_settings(
        config_path=pre.config,
        environment=pre.env,
        force_reload=pre.force_reload,
    )
    settings = get_settings()
    parser = build_main_parser(settings)
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=None)
    pre_parser.add_argument("--env", "-E", default=None)
    pre_parser.add_argument("--force-reload", action="store_true")
    _, rest = pre_parser.parse_known_args(argv)
    args = parser.parse_args(rest)
    merged = apply_cli_overrides(settings, args)
    set_settings(merged)
    return args


def collect_sections(sections_csv: str | None, section_list: list[str] | None) -> list[str]:
    """Collect section filters from arguments."""
    values: list[str] = []
    if sections_csv:
        values.extend(sections_csv.split(","))
    if section_list:
        values.extend(section_list)
    return [value.strip() for value in values if value and value.strip()]


def determine_output_dir(output: str | None, settings: AppSettings | None = None) -> Path:
    """Determine output directory path."""
    s = settings or get_settings()
    if output:
        return Path(output)
    return Path(s.cli_defaults.output_dir)


def _sanitize_for_filesystem(s: str, max_length: int = 220) -> str:
    """Sanitize a string for use in file/directory names (alphanumeric, hyphen, underscore only)."""
    if not s:
        return ""
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in s)
    safe = safe.strip().replace(" ", "-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-")
    if len(safe) > max_length:
        truncated = safe[:max_length]
        last_hyphen = truncated.rfind("-")
        safe = truncated[:last_hyphen] if last_hyphen > max_length * 0.8 else truncated
    return safe


def sanitize_title_for_filesystem(title: str, max_length: int = 220, *, settings: AppSettings | None = None) -> str:
    """Sanitize title for use in file/directory names."""
    s = settings or get_settings()
    unknown = s.output_naming.default_unknown_title
    if not title:
        return unknown
    return _sanitize_for_filesystem(title, max_length)


def build_output_basename(
    submission_date: str | None,
    title: str | None,
    source: str = "Arxiv",
    short: str | None = None,
    *,
    max_title_length: int | None = None,
    max_basename_length: int | None = None,
    settings: AppSettings | None = None,
) -> str:
    """Build output basename for directory and files: [Date]-[Source]-[Short]-[Paper Name]."""
    s = settings or get_settings()
    on = s.output_naming
    max_title_length = max_title_length if max_title_length is not None else on.max_title_length
    max_basename_length = max_basename_length if max_basename_length is not None else on.max_basename_length

    date_str = submission_date if submission_date else on.default_unknown_title
    src_max = on.sanitize_source_max_length
    short_max = on.sanitize_short_max_length
    safe_source = _sanitize_for_filesystem(source, max_length=src_max) or s.cli_defaults.source
    safe_short = _sanitize_for_filesystem(short, max_length=short_max) if short else ""
    safe_title = sanitize_title_for_filesystem(title or on.default_unknown_title, max_length=max_title_length, settings=s)

    if safe_short:
        basename = f"{date_str}-{safe_source}-{safe_short}-{safe_title}"
    else:
        basename = f"{date_str}-{safe_source}-{safe_title}"

    if len(basename) > max_basename_length:
        max_dir_length = max_basename_length
        if safe_short:
            fixed_part = f"{date_str}-{safe_source}-{safe_short}-"
        else:
            fixed_part = f"{date_str}-{safe_source}-"
        max_title_in_basename = max_dir_length - len(fixed_part)
        if len(safe_title) > max_title_in_basename:
            safe_title = sanitize_title_for_filesystem(
                title or on.default_unknown_title,
                max_length=max_title_in_basename,
                settings=s,
            )
        if safe_short:
            basename = f"{date_str}-{safe_source}-{safe_short}-{safe_title}"
        else:
            basename = f"{date_str}-{safe_source}-{safe_title}"

    return basename


def create_paper_output_dir(
    base_output_dir: Path,
    submission_date: str | None,
    title: str | None,
    source: str = "Arxiv",
    short: str | None = None,
    *,
    settings: AppSettings | None = None,
) -> Path:
    """Create output directory for paper with format [date]-[source]-[short]-[title]."""
    s = settings or get_settings()
    dir_name = build_output_basename(submission_date, title, source, short, settings=s)
    output_dir = base_output_dir / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def determine_images_dir(settings: AppSettings | None = None) -> str:
    """Determine images directory name."""
    s = settings or get_settings()
    return s.cli_defaults.images_subdir
