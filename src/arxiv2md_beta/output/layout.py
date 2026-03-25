"""Output directory layout and sanitized basenames for papers (no CLI)."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings


def determine_output_dir(output: str | None, settings: AppSettings | None = None) -> Path:
    """Resolve base output directory from CLI string or config default."""
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
    """Return configured images subdirectory name (e.g. ``images``)."""
    s = settings or get_settings()
    return s.cli_defaults.images_subdir
