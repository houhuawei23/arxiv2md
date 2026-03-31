"""Download and extract TeX source from arXiv or local archive."""

from __future__ import annotations

import asyncio
import gzip
import re
import shutil
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import httpx
from loguru import logger

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.progress import async_byte_download_progress


class TexSourceInfo(NamedTuple):
    """Information about extracted TeX source."""

    extracted_dir: Path
    main_tex_file: Path | None
    image_files: dict[str, Path]  # figure_label -> local_path
    all_images: list[Path]  # All image files found


class TexSourceNotFoundError(Exception):
    """Raised when TeX source is not available."""

    pass


class ImageExtractionError(Exception):
    """Raised when image extraction fails."""

    pass


class ArchiveExtractionError(Exception):
    """Raised when local archive extraction fails."""

    pass


def _cache_dir_for(arxiv_id: str, version: str | None) -> Path:
    """Get cache directory for arXiv ID."""
    base = arxiv_id
    if version and arxiv_id.endswith(version):
        base = arxiv_id[: -len(version)]
    version_tag = version or "latest"
    key = f"{base}__{version_tag}".replace("/", "_")
    return get_settings().resolved_cache_path() / key


async def fetch_and_extract_tex_source(
    arxiv_id: str,
    version: str | None = None,
    use_cache: bool = True,
) -> TexSourceInfo:
    """Download and extract TeX source from arXiv.

    Parameters
    ----------
    arxiv_id : str
        arXiv ID (e.g., "2501.11120" or "2501.11120v1")
    version : str | None
        Version string (e.g., "v1")
    use_cache : bool
        Whether to use cached files if available

    Returns
    -------
    TexSourceInfo
        Information about extracted TeX source including images

    Raises
    ------
    TexSourceNotFoundError
        If TeX source is not available
    ImageExtractionError
        If image extraction fails
    """
    cache_dir = _cache_dir_for(arxiv_id, version)
    tex_source_path = cache_dir / "tex_source.tar.gz"
    extracted_dir = cache_dir / "tex_extracted"

    # Check cache: use tarball mtime for TTL, not extracted_dir — tar.extractall
    # restores directory mtimes from the archive, so tex_extracted can look
    # "days old" immediately after a fresh download and falsely fail TTL.
    if (
        use_cache
        and tex_source_path.exists()
        and extracted_dir.exists()
        and _has_tex_files(extracted_dir)
        and _mtime_within_ttl(tex_source_path)
    ):
        logger.info(f"Using cached TeX source for {arxiv_id}")
        return _extract_info_from_dir(extracted_dir)

    # Download TeX source
    tex_url = get_settings().urls.arxiv_src_template.format(arxiv_id=arxiv_id)
    logger.info(f"Downloading TeX source from {tex_url}")

    try:
        await _download_tex_source(tex_url, tex_source_path)
    except RuntimeError as e:
        raise TexSourceNotFoundError(
            f"Failed to download TeX source for {arxiv_id}: {e}"
        ) from e

    # Extract archive
    logger.info(f"Extracting TeX source to {extracted_dir}")
    try:
        extracted_dir.mkdir(parents=True, exist_ok=True)
        _extract_archive(tex_source_path, extracted_dir)
    except Exception as e:
        raise ImageExtractionError(f"Failed to extract TeX source: {e}") from e

    # Extract images and find main tex file
    return _extract_info_from_dir(extracted_dir)


def extract_local_archive(
    archive_path: Path,
    output_dir: Path | None = None,
    use_cache: bool = True,
) -> TexSourceInfo:
    """Extract a local archive file (tar.gz, tgz, or zip).

    Parameters
    ----------
    archive_path : Path
        Path to the local archive file
    output_dir : Path | None
        Directory to extract to. If None, uses cache directory.
    use_cache : bool
        Whether to use cached extraction if available

    Returns
    -------
    TexSourceInfo
        Information about extracted source including images

    Raises
    ------
    ArchiveExtractionError
        If extraction fails
    FileNotFoundError
        If archive file doesn't exist
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    # Determine extraction directory
    if output_dir is None:
        # Create a cache key based on file path and modification time
        mtime = archive_path.stat().st_mtime
        cache_key = f"local_{archive_path.stem}_{int(mtime)}"
        extracted_dir = get_settings().resolved_cache_path() / cache_key / "extracted"
    else:
        extracted_dir = output_dir

    # Check cache: use archive file mtime for TTL (same reason as arXiv TeX cache)
    if use_cache and extracted_dir.exists() and _mtime_within_ttl(archive_path):
        if _has_tex_files(extracted_dir):
            logger.info(f"Using cached extraction for {archive_path.name}")
            return _extract_info_from_dir(extracted_dir)

    logger.info(f"Extracting local archive: {archive_path}")

    try:
        extracted_dir.mkdir(parents=True, exist_ok=True)

        # Determine archive type and extract
        if archive_path.suffix.lower() == ".zip" or str(archive_path).lower().endswith(".zip"):
            _extract_zip_archive(archive_path, extracted_dir)
        elif (
            archive_path.suffix.lower() in (".gz", ".tgz")
            or str(archive_path).lower().endswith(".tar.gz")
            or str(archive_path).lower().endswith(".tgz")
        ):
            _extract_tar_archive(archive_path, extracted_dir)
        else:
            raise ArchiveExtractionError(f"Unsupported archive format: {archive_path.suffix}")

    except Exception as e:
        raise ArchiveExtractionError(f"Failed to extract archive: {e}") from e

    # Extract images and find main tex file
    return _extract_info_from_dir(extracted_dir)


def _extract_zip_archive(archive_path: Path, output_dir: Path) -> None:
    """Extract a ZIP archive.

    Parameters
    ----------
    archive_path : Path
        Path to ZIP file
    output_dir : Path
        Directory to extract to

    Raises
    ------
    ArchiveExtractionError
        If extraction fails
    """
    try:
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            # Check for potential zip bomb / path traversal
            for member in zip_ref.namelist():
                member_path = output_dir / member
                try:
                    member_path.relative_to(output_dir.resolve())
                except ValueError:
                    raise ArchiveExtractionError(
                        f"Archive contains suspicious path: {member}"
                    )

            zip_ref.extractall(output_dir)
        logger.info(f"Extracted ZIP archive to {output_dir}")
    except zipfile.BadZipFile as e:
        raise ArchiveExtractionError(f"Invalid ZIP file: {e}") from e
    except Exception as e:
        raise ArchiveExtractionError(f"Failed to extract ZIP: {e}") from e


def _extract_tar_archive(archive_path: Path, output_dir: Path) -> None:
    """Extract a tar.gz or .tgz archive.

    Parameters
    ----------
    archive_path : Path
        Path to tar archive
    output_dir : Path
        Directory to extract to

    Raises
    ------
    ArchiveExtractionError
        If extraction fails
    """
    try:
        # Handle both .tar.gz and single .gz files
        if archive_path.suffix.lower() == ".gz" and not str(archive_path).lower().endswith(".tar.gz"):
            # Single gzipped file
            with gzip.open(archive_path, "rb") as f_in:
                output_file = output_dir / archive_path.stem
                with open(output_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
        else:
            # tar.gz archive
            with tarfile.open(archive_path, "r:gz") as tar:
                # Security: check for path traversal
                for member in tar.getmembers():
                    member_path = output_dir / member.name
                    try:
                        member_path.relative_to(output_dir.resolve())
                    except ValueError:
                        raise ArchiveExtractionError(
                            f"Archive contains suspicious path: {member.name}"
                        )
                tar.extractall(output_dir)
        logger.info(f"Extracted tar archive to {output_dir}")
    except tarfile.TarError as e:
        raise ArchiveExtractionError(f"Invalid tar file: {e}") from e
    except Exception as e:
        raise ArchiveExtractionError(f"Failed to extract tar: {e}") from e


async def _download_tex_source(url: str, output_path: Path) -> None:
    """Download TeX source with retries and progress bar."""
    s = get_settings()
    h = s.http
    retry_status = set(h.retry_status_codes)
    timeout = httpx.Timeout(h.fetch_timeout_s * h.large_transfer_timeout_multiplier)
    headers = {"User-Agent": h.user_agent}
    last_exc: Exception | None = None

    for attempt in range(h.fetch_max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        raise RuntimeError(
                            f"TeX source not found at {url}. "
                            "This paper may not have TeX source available."
                        )

                    if response.status_code in retry_status:
                        last_exc = RuntimeError(f"HTTP {response.status_code} from arXiv")
                    else:
                        response.raise_for_status()

                        # Get content length for progress bar
                        total_size = int(response.headers.get("content-length", 0))

                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        disable_tqdm = s.images.disable_tqdm

                        async with async_byte_download_progress(
                            "Downloading TeX source",
                            total_size if total_size > 0 else None,
                            disable=disable_tqdm,
                        ) as advance:
                            with open(output_path, "wb") as f:
                                async for chunk in response.aiter_bytes():
                                    f.write(chunk)
                                    advance(len(chunk))

                        return
        except (httpx.RequestError, httpx.HTTPStatusError, RuntimeError) as exc:
            last_exc = exc

        if attempt < h.fetch_max_retries:
            backoff = h.fetch_backoff_s * (2**attempt)
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Failed to download TeX source from {url}: {last_exc}")


def _extract_archive(archive_path: Path, output_dir: Path) -> None:
    """Extract tar.gz or gz archive."""
    if archive_path.suffix == ".gz" and not archive_path.suffixes[-2:] == [".tar", ".gz"]:
        # Single gzipped file
        with gzip.open(archive_path, "rb") as f_in:
            output_file = output_dir / archive_path.stem
            with open(output_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    else:
        # tar.gz archive
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(output_dir)


def _extract_info_from_dir(extracted_dir: Path) -> TexSourceInfo:
    """Extract information from extracted TeX source directory."""
    # Find all image files
    image_extensions = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".ps"}
    all_images: list[Path] = []
    for ext in image_extensions:
        all_images.extend(extracted_dir.rglob(f"*{ext}"))
        all_images.extend(extracted_dir.rglob(f"*{ext.upper()}"))

    # Find main tex file
    main_tex = _find_main_tex_file(extracted_dir)

    # Build image map from tex files
    image_map: dict[str, Path] = {}
    if main_tex:
        image_map = _parse_images_from_tex(main_tex, extracted_dir, all_images)

    return TexSourceInfo(
        extracted_dir=extracted_dir,
        main_tex_file=main_tex,
        image_files=image_map,
        all_images=all_images,
    )


def _find_main_tex_file(extracted_dir: Path) -> Path | None:
    """Find the main LaTeX file (root document with \\documentclass).

    Looks for:
    1. Files named main.tex, paper.tex, article.tex
    2. Root document (contains \\documentclass and \\begin{document})
    3. Files matching arXiv ID pattern
    4. Largest .tex file in root dir only (not in subdirs)
    5. Any .tex file if only one exists
    """
    tex_files = list(extracted_dir.rglob("*.tex"))

    if not tex_files:
        return None

    if len(tex_files) == 1:
        return tex_files[0]

    # Check for common main file names
    for name in ["main.tex", "paper.tex", "article.tex"]:
        candidate = extracted_dir / name
        if candidate.exists():
            return candidate

    # Prefer root document (has \\documentclass) - required for correct \\input order
    for tex_file in tex_files:
        if tex_file.parent != extracted_dir:
            continue
        try:
            content = tex_file.read_text(encoding="utf-8", errors="ignore")
            if "\\documentclass" in content and "\\begin{document}" in content:
                return tex_file
        except Exception:
            pass

    # Check for arXiv ID pattern in filename
    arxiv_id_pattern = re.compile(r"\d{4}\.\d{4,5}")
    for tex_file in tex_files:
        if arxiv_id_pattern.search(tex_file.name):
            return tex_file

    # Use largest file in root dir (main file with includes, not section files)
    root_tex = [p for p in tex_files if p.parent == extracted_dir]
    if root_tex:
        return max(root_tex, key=lambda p: p.stat().st_size)
    return max(tex_files, key=lambda p: p.stat().st_size)


def _expand_tex_includes(
    tex_file: Path, base_dir: Path, visited: set[Path] | None = None
) -> str:
    """Expand \\input and \\include in document order for image extraction."""
    if visited is None:
        visited = set()
    if tex_file in visited:
        return ""
    visited.add(tex_file)
    if not tex_file.exists():
        return ""
    content = tex_file.read_text(encoding="utf-8", errors="ignore")
    include_pattern = re.compile(r"\\(?:input|include)\{([^}]+)\}")

    def replace_include(match: re.Match[str]) -> str:
        start = content.rfind("\n", 0, match.start()) + 1
        if content[start:match.start()].strip().startswith("%"):
            return match.group(0)
        name = match.group(1).strip()
        stem = name[:-4] if name.endswith(".tex") else name
        for cand in [base_dir / name, base_dir / f"{stem}.tex", base_dir / stem]:
            if cand.exists() and cand.is_file():
                return _expand_tex_includes(cand, base_dir, visited)
        for p in base_dir.rglob(Path(name).name):
            if p.is_file():
                return _expand_tex_includes(p, base_dir, visited)
        return ""

    return include_pattern.sub(replace_include, content)


def _parse_images_from_tex(
    tex_file: Path, base_dir: Path, all_images: list[Path]
) -> dict[str, Path]:
    """Parse image references from LaTeX file in document order.

    Expands \\input/\\include recursively so images in included files are found.
    Returns ordered mapping (label -> path) matching HTML figure order (x1, x2, ...).
    """
    expanded = _expand_tex_includes(tex_file, base_dir)
    includegraphics_pattern = re.compile(
        r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}"
    )
    image_map: dict[str, Path] = {}
    seen_paths: set[Path] = set()
    counter = 0

    for match in includegraphics_pattern.finditer(expanded):
        line_start = expanded.rfind("\n", 0, match.start()) + 1
        if expanded[line_start:match.start()].strip().startswith("%"):
            continue
        image_path_str = match.group(1).strip()
        image_path = _resolve_image_path(image_path_str, base_dir, all_images)
        if image_path and image_path not in seen_paths:
            seen_paths.add(image_path)
            image_map[f"fig_{counter}"] = image_path
            counter += 1

    return image_map


def _resolve_image_path(
    image_path_str: str, base_dir: Path, all_images: list[Path]
) -> Path | None:
    """Resolve image path from LaTeX reference to actual file."""
    # Remove common LaTeX path prefixes
    image_path_str = image_path_str.strip()
    if image_path_str.startswith("./"):
        image_path_str = image_path_str[2:]

    # Try direct match
    candidate = base_dir / image_path_str
    if candidate.exists() and candidate in all_images:
        return candidate

    # Try with common extensions
    base_name = Path(image_path_str).stem
    for img_path in all_images:
        if img_path.stem == base_name:
            return img_path

    # Try case-insensitive match
    base_name_lower = base_name.lower()
    for img_path in all_images:
        if img_path.stem.lower() == base_name_lower:
            return img_path

    return None


def _has_tex_files(extracted_dir: Path) -> bool:
    """True if extraction looks complete (at least one .tex file)."""
    return any(extracted_dir.rglob("*.tex"))


def _mtime_within_ttl(path: Path) -> bool:
    """Whether ``path``'s mtime is within ``cache.ttl_seconds`` (authoritative for downloads)."""
    if not path.exists():
        return False
    ttl = get_settings().cache.ttl_seconds
    if ttl <= 0:
        return True
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - mtime).total_seconds()
    return age_seconds <= ttl
