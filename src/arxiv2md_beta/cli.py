"""Command-line interface for arxiv2md-beta."""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
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
        default="html",
        help="Parser mode: html (default) or latex",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory path. A subdirectory will be created with format '[date]-[source]-[short]-[title]'",
    )
    parser.add_argument(
        "--source",
        default="Arxiv",
        help="Article source (conference/journal name). Default: Arxiv",
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
        default="exclude",
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
    return parser.parse_args()


def collect_sections(sections_csv: str | None, section_list: list[str] | None) -> list[str]:
    """Collect section filters from arguments."""
    values: list[str] = []
    if sections_csv:
        values.extend(sections_csv.split(","))
    if section_list:
        values.extend(section_list)
    return [value.strip() for value in values if value and value.strip()]


def determine_output_dir(output: str | None) -> Path:
    """Determine output directory path."""
    if output:
        return Path(output)
    # Default: current directory
    return Path(".")


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


def sanitize_title_for_filesystem(title: str, max_length: int = 220) -> str:
    """Sanitize title for use in file/directory names.
    
    Parameters
    ----------
    title : str
        Original title
    max_length : int
        Maximum length for the sanitized title
        
    Returns
    -------
    str
        Sanitized title safe for filesystem use
    """
    if not title:
        return "Unknown"
    return _sanitize_for_filesystem(title, max_length)


def build_output_basename(
    submission_date: str | None,
    title: str | None,
    source: str = "Arxiv",
    short: str | None = None,
    max_title_length: int = 220,
    max_basename_length: int = 255,
) -> str:
    """Build output basename for directory and files: [Date]-[Source]-[Short]-[Paper Name].
    
    Parameters
    ----------
    submission_date : str | None
        Submission date in YYYYMMDD format
    title : str | None
        Paper title
    source : str
        Article source (conference/journal name)
    short : str | None
        Short name for the article (optional)
    max_title_length : int
        Maximum length for the sanitized title part
        
    Returns
    -------
    str
        Basename for directory and files (without extension)
    """
    date_str = submission_date if submission_date else "Unknown"
    safe_source = _sanitize_for_filesystem(source, max_length=80) or "Arxiv"
    safe_short = _sanitize_for_filesystem(short, max_length=80) if short else ""
    safe_title = sanitize_title_for_filesystem(title or "Unknown", max_length=max_title_length)
    
    if safe_short:
        basename = f"{date_str}-{safe_source}-{safe_short}-{safe_title}"
    else:
        basename = f"{date_str}-{safe_source}-{safe_title}"
    
    # Final safety check: ensure total length doesn't exceed filesystem limits
    if len(basename) > max_basename_length:
        max_dir_length = max_basename_length
        if safe_short:
            fixed_part = f"{date_str}-{safe_source}-{safe_short}-"
        else:
            fixed_part = f"{date_str}-{safe_source}-"
        max_title_in_basename = max_dir_length - len(fixed_part)
        if len(safe_title) > max_title_in_basename:
            safe_title = sanitize_title_for_filesystem(title or "Unknown", max_length=max_title_in_basename)
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
) -> Path:
    """Create output directory for paper with format [date]-[source]-[short]-[title].
    
    Parameters
    ----------
    base_output_dir : Path
        Base output directory
    submission_date : str | None
        Submission date in YYYYMMDD format
    title : str | None
        Paper title
    source : str
        Article source (conference/journal name)
    short : str | None
        Short name for the article (optional)
    
    Returns
    -------
    Path
        Created output directory path
    """
    dir_name = build_output_basename(submission_date, title, source, short)
    output_dir = base_output_dir / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def determine_images_dir() -> str:
    """Determine images directory name."""
    return "images"
