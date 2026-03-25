"""Parse and normalize arXiv inputs and local archive paths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final
from urllib.parse import urlparse
from uuid import uuid4

from arxiv2md_beta.schemas import ArxivQuery, LocalArchiveQuery
from arxiv2md_beta.settings import get_settings

_ARXIV_PATH_KINDS: Final = {"abs", "pdf", "html"}
_ARXIV_ID_RE: Final = re.compile(
    r"^(?P<base>(\d{4}\.\d{4,5}|[a-zA-Z-]+/\d{7}))(v(?P<version>\d+))?$",
)


def parse_arxiv_input(input_text: str) -> ArxivQuery:
    """Parse a raw arXiv ID or URL into a normalized query object."""
    raw = input_text.strip()
    if not raw:
        raise ValueError("input_text cannot be empty")

    normalized_id, version = _extract_arxiv_id(raw)
    s = get_settings()
    host = s.urls.arxiv_host
    html_url = f"https://{host}/html/{normalized_id}"
    ar5iv_url = f"{s.urls.ar5iv_html_base.rstrip('/')}/{normalized_id}"
    abs_url = f"https://{host}/abs/{normalized_id}"
    query_id = uuid4()

    return ArxivQuery(
        input_text=raw,
        arxiv_id=normalized_id,
        version=version,
        html_url=html_url,
        ar5iv_url=ar5iv_url,
        abs_url=abs_url,
        id=query_id,
        cache_dir=s.resolved_cache_path() / str(query_id),
    )


def parse_local_archive(input_text: str) -> LocalArchiveQuery:
    """Parse a local archive path (tar.gz, tgz, or zip) into a query object.

    Parameters
    ----------
    input_text : str
        Path to local archive file (e.g., /path/to/paper.tar.gz or /path/to/paper.zip)

    Returns
    -------
    LocalArchiveQuery
        Parsed query object

    Raises
    ------
    ValueError
        If the path is not a valid archive file
    FileNotFoundError
        If the archive file does not exist
    """
    raw = input_text.strip()
    if not raw:
        raise ValueError("input_text cannot be empty")

    archive_path = Path(raw).expanduser().resolve()

    if not archive_path.exists():
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    if not archive_path.is_file():
        raise ValueError(f"Path is not a file: {archive_path}")

    # Determine archive type from extension
    archive_type = _get_archive_type(archive_path)
    if not archive_type:
        raise ValueError(
            f"Unsupported archive format: {archive_path.suffix}. "
            "Supported formats: .tar.gz, .tgz, .zip"
        )

    query_id = uuid4()

    return LocalArchiveQuery(
        input_text=raw,
        archive_path=archive_path,
        archive_type=archive_type,  # type: ignore[arg-type]
        id=query_id,
        cache_dir=get_settings().resolved_cache_path() / str(query_id),
    )


def is_local_archive_path(input_text: str) -> bool:
    """Check if input looks like a local archive path.

    Parameters
    ----------
    input_text : str
        Input string to check

    Returns
    -------
    bool
        True if input appears to be a local archive path
    """
    raw = input_text.strip()
    if not raw:
        return False

    # Check if it looks like a path (contains / or starts with . or ~)
    # and ends with supported archive extension
    if "/" in raw or raw.startswith((".", "~", "/")):
        lower = raw.lower()
        if lower.endswith((".tar.gz", ".tgz", ".zip")):
            return True

    # Also check if it's an existing file
    try:
        path = Path(raw).expanduser()
        if path.exists() and path.is_file():
            return _get_archive_type(path) is not None
    except (OSError, ValueError):
        pass

    return False


def _get_archive_type(path: Path) -> str | None:
    """Determine archive type from file path.

    Parameters
    ----------
    path : Path
        File path to check

    Returns
    -------
    str | None
        Archive type ("tar.gz", "tgz", "zip") or None if not recognized
    """
    name_lower = path.name.lower()

    if name_lower.endswith(".tar.gz"):
        return "tar.gz"
    elif name_lower.endswith(".tgz"):
        return "tgz"
    elif name_lower.endswith(".zip"):
        return "zip"

    return None


def _extract_arxiv_id(raw: str) -> tuple[str, str | None]:
    """Extract and normalize an arXiv identifier from raw input."""
    cleaned = _strip_arxiv_prefix(raw)
    if _looks_like_url(cleaned):
        return _extract_from_url(cleaned)
    return _normalize_id(cleaned)


def _strip_arxiv_prefix(value: str) -> str:
    if value.lower().startswith("arxiv:"):
        return value.split(":", 1)[1].strip()
    return value


def _looks_like_url(value: str) -> bool:
    # Check for full URLs
    if value.startswith(("http://", "https://", "arxiv.org/")):
        return True
    # Check for path-style inputs like "html/2501.11120v1" or "abs/2501.11120v1"
    if "/" in value:
        first_part = value.split("/")[0]
        if first_part in _ARXIV_PATH_KINDS:
            return True
    return False


def _extract_from_url(url: str) -> tuple[str, str | None]:
    # Handle path-style inputs like "html/2501.11120v1" or "abs/2501.11120v1"
    if not url.startswith(("http://", "https://", "arxiv.org/")):
        first_part = url.split("/")[0]
        if first_part in _ARXIV_PATH_KINDS:
            url = f"https://arxiv.org/{url}"

    if url.startswith("arxiv.org/"):
        url = f"https://{url}"

    parsed = urlparse(url)
    host = get_settings().urls.arxiv_host
    if parsed.netloc and host not in parsed.netloc:
        raise ValueError(f"Unsupported host: {parsed.netloc}")

    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError("Invalid arXiv URL: missing path")

    if path_parts[0] in _ARXIV_PATH_KINDS and len(path_parts) >= 2:
        kind = path_parts[0]
        arxiv_part = path_parts[1]
        if kind == "pdf" and arxiv_part.endswith(".pdf"):
            arxiv_part = arxiv_part[: -len(".pdf")]
        return _normalize_id(arxiv_part)

    # Accept direct paths like /<id> or /<id>vN
    return _normalize_id(path_parts[0])


def _normalize_id(value: str) -> tuple[str, str | None]:
    match = _ARXIV_ID_RE.match(value)
    if not match:
        raise ValueError(f"Unrecognized arXiv identifier: {value}")

    base = match.group("base")
    version_digits = match.group("version")
    version = f"v{version_digits}" if version_digits else None
    normalized = f"{base}{version}" if version else base
    return normalized, version
