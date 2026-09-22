"""Download and extract TeX source from arXiv or local archive."""

from __future__ import annotations

import asyncio
import gzip
import re
import shutil
import tarfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import aiofiles
import httpx
from loguru import logger

from arxiv2md_beta.exceptions import ImageProcessingError, NetworkError, NonRetryableNetworkError, StorageError
from arxiv2md_beta.latex.includes import _after_unescaped_comment
from arxiv2md_beta.network.http import acquire_rate_slot, get_http_client, http_request_slot
from arxiv2md_beta.network.mirror import mirror_worth_try, to_export_mirror
from arxiv2md_beta.network.retry import compute_backoff
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.progress import async_byte_download_progress


class TexSourceInfo(NamedTuple):
    """Information about extracted TeX source."""

    extracted_dir: Path
    main_tex_file: Path | None
    image_files: dict[str, Path]  # figure_label -> local_path
    all_images: list[Path]  # All image files found
    # \includegraphics inside \begin{figure} envs only, in float order. Drives
    # the figure-index map so ar5iv xN.png names resolve to the right file.
    figure_image_files: list[Path] = []


class TexSourceNotFoundError(NetworkError):
    """Raised when TeX source is not available (HTTP 404 or download exhausted)."""

    pass


def _file_is_pdf(path: Path) -> bool:
    """Sniff the 5-byte PDF magic without reading the whole file (audit5 R-2)."""
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"


def _safe_archive_target(output_dir: Path, member_name: str) -> Path:
    """Return a normalized extraction target contained by *output_dir*."""
    if not member_name or Path(member_name).is_absolute():
        raise ArchiveExtractionError(f"Archive contains suspicious path: {member_name}")
    root = output_dir.resolve()
    target = (root / member_name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ArchiveExtractionError(f"Archive contains suspicious path: {member_name}") from exc
    return target


class ImageExtractionError(ImageProcessingError):
    """Raised when image extraction fails."""

    pass


class ArchiveExtractionError(StorageError):
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

    Returns:
    -------
    TexSourceInfo
        Information about extracted TeX source including images

    Raises:
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
        info = _extract_info_from_dir(extracted_dir)
        _log_tex_source_paths(arxiv_id, cache_dir, extracted_dir, tex_source_path, info)
        return info

    # Download TeX source
    tex_url = get_settings().urls.arxiv_src_template.format(arxiv_id=arxiv_id)
    logger.info(f"Downloading TeX source from {tex_url}")

    async def _download_with_mirror() -> None:
        try:
            await _download_tex_source(tex_url, tex_source_path)
        except NetworkError as tex_error:
            # Mirror fallback: export.arxiv.org serves /src/ from its own
            # backend with independent rate limiting.
            mirrored = to_export_mirror(tex_url)
            if not (mirrored and mirror_worth_try(tex_error)):
                raise
            logger.warning(f"Retrying TeX source download via export mirror: {mirrored}")
            try:
                await _download_tex_source(mirrored, tex_source_path)
            except NetworkError as mirror_error:
                logger.warning(f"Export mirror TeX fallback also failed: {mirror_error}")
                raise tex_error from mirror_error

    try:
        await _download_with_mirror()
    except RuntimeError as e:
        raise TexSourceNotFoundError(f"Failed to download TeX source for {arxiv_id}: {e}") from e

    # Extract archive into a task-private temp dir, then move into place.
    # Two concurrent conversions of the same paper share the cache path; a
    # shared extract dir let one task's failure rmtree delete the other's
    # freshly extracted files mid-read.
    logger.info(f"Extracting TeX source to {extracted_dir}")
    staging_dir = extracted_dir.with_name(f"{extracted_dir.name}.tmp-{uuid.uuid4().hex}")
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_extract_archive, tex_source_path, staging_dir)
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir, ignore_errors=True)
        staging_dir.replace(extracted_dir)
    except Exception as e:
        # Remove only our own partial extract — never a shared directory.
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ImageExtractionError(f"Failed to extract TeX source: {e}") from e

    # Extract images and find main tex file (rglob + per-file reads are IO-bound)
    info = await asyncio.to_thread(_extract_info_from_dir, extracted_dir)
    _log_tex_source_paths(arxiv_id, cache_dir, extracted_dir, tex_source_path, info)
    return info


def _log_tex_source_paths(
    arxiv_id: str,
    cache_dir: Path,
    extracted_dir: Path,
    tex_archive_path: Path,
    info: TexSourceInfo,
) -> None:
    """Log cache session directory and extracted tree for user inspection."""
    logger.info(
        f"TeX paths for {arxiv_id}: cache_dir={cache_dir} | "
        f"extracted_dir={extracted_dir} | tarball={tex_archive_path.name} | "
        f"main_tex={info.main_tex_file}"
    )


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

    Returns:
    -------
    TexSourceInfo
        Information about extracted source including images

    Raises:
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
    if use_cache and extracted_dir.exists() and _mtime_within_ttl(archive_path) and _has_tex_files(extracted_dir):
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
        # Remove the partial extract so a later run does not mistake a half-
        # extracted directory for a valid cache entry (cache poisoning).
        shutil.rmtree(extracted_dir, ignore_errors=True)
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

    Raises:
    ------
    ArchiveExtractionError
        If extraction fails
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            # Check for potential zip bomb / path traversal
            for member in zip_ref.infolist():
                _safe_archive_target(output_dir, member.filename)
                unix_mode = member.external_attr >> 16
                if (unix_mode & 0o170000) == 0o120000:
                    raise ArchiveExtractionError(f"Archive contains symbolic link: {member.filename}")

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

    Raises:
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
                members = tar.getmembers()
                for member in members:
                    _safe_archive_target(output_dir, member.name)
                    if member.issym() or member.islnk():
                        raise ArchiveExtractionError(f"Archive contains link: {member.name}")
                    if not (member.isfile() or member.isdir()):
                        raise ArchiveExtractionError(f"Archive contains unsupported member: {member.name}")
                tar.extractall(output_dir, members=members)
        logger.info(f"Extracted tar archive to {output_dir}")
    except tarfile.TarError as e:
        raise ArchiveExtractionError(f"Invalid tar file: {e}") from e
    except Exception as e:
        raise ArchiveExtractionError(f"Failed to extract tar: {e}") from e


async def _download_tex_source(url: str, output_path: Path) -> None:
    """Download TeX source with retries and progress bar."""
    s = get_settings()
    h = s.http
    timeout = httpx.Timeout(h.fetch_timeout_s * h.large_transfer_timeout_multiplier)
    last_exc: Exception | None = None
    last_status: int | None = None

    client = get_http_client()
    non_retryable = set(h.non_retryable_status_codes)
    for attempt in range(h.fetch_max_retries + 1):
        try:
            await acquire_rate_slot()
            async with http_request_slot(), client.stream("GET", url, timeout=timeout) as response:
                if response.status_code == 404:
                    raise TexSourceNotFoundError(
                        f"TeX source not found at {url}. This paper may not have TeX source available.",
                        status_code=404,
                    )

                if response.status_code in non_retryable:
                    # Permanent (403 ban, 410 withdrawn...): give up at once.
                    # NetworkError subtype → orchestrator degrades to a
                    # no-images conversion instead of failing the paper.
                    raise NonRetryableNetworkError(
                        f"HTTP {response.status_code} from arXiv", status_code=response.status_code
                    )

                if response.status_code >= 400:
                    last_status = response.status_code
                    last_exc = RuntimeError(f"HTTP {response.status_code} from arXiv")
                else:
                    # Some papers have no TeX source (PDF-only submission); arXiv
                    # then serves the rendered PDF from /src/ with HTTP 200, not
                    # 404. Detect it so callers take the no-TeX fallback path
                    # instead of failing tar extraction later.
                    content_type = response.headers.get("content-type", "").lower()
                    if "pdf" in content_type:
                        raise TexSourceNotFoundError(
                            f"TeX source not available for this paper (arXiv served a PDF from {url}); "
                            "the paper was likely submitted as PDF-only."
                        )

                    # Get content length for progress bar; malformed headers
                    # (proxy noise) degrade to an indeterminate bar.
                    try:
                        total_size = int(response.headers.get("content-length", 0))
                    except (TypeError, ValueError):
                        total_size = 0
                    total_size = max(0, total_size)

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    disable_tqdm = s.images.disable_tqdm

                    # Write to a temp sibling then rename so a concurrent
                    # conversion never sees (or overwrites) a half-written cache.
                    tmp_path = output_path.with_name(f"{output_path.name}.{uuid.uuid4().hex}.part")
                    try:
                        # The download itself stays inside the try: a
                        # mid-stream RequestError used to leave the orphan
                        # .part behind (audit5 R-1).
                        async with (
                            async_byte_download_progress(
                                "Downloading TeX source",
                                total_size if total_size > 0 else None,
                                disable=disable_tqdm,
                            ) as advance,
                            aiofiles.open(tmp_path, "wb") as f,
                        ):
                            async for chunk in response.aiter_bytes():
                                await f.write(chunk)
                                advance(len(chunk))

                        # Belt-and-suspenders: header-based check above can miss
                        # PDF-only papers when content-type is generic. Sniff magic
                        # bytes so the bogus file never lands in the cache.
                        if await asyncio.to_thread(_file_is_pdf, tmp_path):
                            raise TexSourceNotFoundError(
                                f"TeX source not available for this paper "
                                f"(arXiv served a PDF from {url}); the paper was likely submitted as PDF-only."
                            )
                        tmp_path.replace(output_path)
                        return
                    finally:
                        tmp_path.unlink(missing_ok=True)
        except (httpx.RequestError, httpx.HTTPStatusError, RuntimeError) as exc:
            last_exc = exc

        if attempt < h.fetch_max_retries:
            backoff = compute_backoff(h.fetch_backoff_s, attempt)
            await asyncio.sleep(backoff)

    raise TexSourceNotFoundError(f"Failed to download TeX source from {url}: {last_exc}", status_code=last_status)


def _extract_archive(archive_path: Path, output_dir: Path) -> None:
    """Extract downloaded tar.gz or gz archive.

    Delegates to :func:`_extract_tar_archive` so the arXiv-downloaded tarball
    gets the same path-traversal protection as local archives (previously it
    called ``tar.extractall`` unchecked and duplicated the gz/tar logic).
    """
    _extract_tar_archive(archive_path, output_dir)


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
    figure_image_files: list[Path] = []
    if main_tex:
        image_map = _parse_images_from_tex(main_tex, extracted_dir, all_images)
        figure_image_files = _parse_figure_env_images_from_tex(main_tex, extracted_dir, all_images)

    return TexSourceInfo(
        extracted_dir=extracted_dir,
        main_tex_file=main_tex,
        image_files=image_map,
        all_images=all_images,
        figure_image_files=figure_image_files,
    )


def _find_main_tex_file(extracted_dir: Path) -> Path | None:
    r"""Find the main LaTeX file (root document with \\documentclass).

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


def _expand_tex_includes(tex_file: Path, base_dir: Path, stack: set[Path] | None = None) -> str:
    r"""Expand \\input and \\include in document order for image extraction.

    *stack* holds the active recursion chain (not a global visited set): the
    same file legitimately included by two sibling subtrees expands both
    times, while a true cycle still terminates (audit4 P2).
    """
    if stack is None:
        stack = set()
    if tex_file in stack:
        return ""
    stack.add(tex_file)
    if not tex_file.exists():
        stack.discard(tex_file)
        return ""
    content = tex_file.read_text(encoding="utf-8", errors="ignore")
    include_pattern = re.compile(r"\\(?:input|include)\{([^}]+)\}")

    def replace_include(match: re.Match[str]) -> str:
        # Mid-line comments hide includes too ("foo % \input{x}") — reuse
        # the escape-aware check (audit5 R-4).
        if _after_unescaped_comment(content, match.start()):
            return match.group(0)
        name = match.group(1).strip()
        stem = name[:-4] if name.endswith(".tex") else name
        for cand in [base_dir / name, base_dir / f"{stem}.tex", base_dir / stem]:
            # Containment check mirrors includes.py (audit5 G3-4): a
            # "../.." path must not read files outside the archive.
            if cand.exists() and cand.is_file() and cand.resolve().is_relative_to(base_dir.resolve()):
                return _expand_tex_includes(cand, base_dir, stack)
        for p in base_dir.rglob(Path(name).name):
            # rglob patterns with ".." escape base_dir; check containment
            # (audit5 G3-4)
            if p.is_file() and p.resolve().is_relative_to(base_dir.resolve()):
                return _expand_tex_includes(p, base_dir, stack)
        return ""

    expanded = include_pattern.sub(replace_include, content)
    stack.discard(tex_file)
    return expanded


def expand_tex_source_for_parsing(tex_source_info: TexSourceInfo) -> str:
    r"""Expand ``\\input`` / ``\\include`` from the main ``.tex`` for author/affiliation parsing."""
    if not tex_source_info.main_tex_file:
        return ""
    return _expand_tex_includes(tex_source_info.main_tex_file, tex_source_info.extracted_dir)


def _find_matching_brace_end(text: str, open_brace_idx: int) -> int | None:
    """Return index of ``}`` that closes ``{`` at ``open_brace_idx`` (nested ``{}`` aware)."""
    if open_brace_idx >= len(text) or text[open_brace_idx] != "{":
        return None
    depth = 0
    i = open_brace_idx
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _strip_title_blocks_for_image_extraction(text: str) -> str:
    r"""Remove ``\\icmltitle{...}`` and ``\\title{...}`` blocks from TeX for image ordering.

    ICML and similar templates often put a logo via ``\\includegraphics`` inside
    ``\\icmltitle{...}``. ar5iv HTML does not emit that graphic as ``Figure~1``; it
    renames body figures to ``x1.png``, ``x2.png``, … in **document** order. If we
    keep title graphics in the TeX raster list, ``image_map[0]`` is the logo while
    HTML ``x1`` is the first real figure (e.g. teaser), so positional fallback pairs
    the wrong file with each caption.
    """
    # Longer command names share prefixes (e.g. \\icmltitlerunning); only strip bare macros.
    icmltitle = "\\icmltitle"
    title_cmd = "\\title"
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        stripped = False
        for cmd in (icmltitle, title_cmd):
            if not text.startswith(cmd, i):
                continue
            j = i + len(cmd)
            if j < n and text[j].isalpha():
                continue  # icmltitlerunning, titlepage, …
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] == "[":
                depth = 1
                j += 1
                while j < n and depth > 0:
                    if text[j] == "[":
                        depth += 1
                    elif text[j] == "]":
                        depth -= 1
                    j += 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] != "{":
                continue
            end = _find_matching_brace_end(text, j)
            if end is None:
                continue
            out.append(" " * (end - i + 1))
            i = end + 1
            stripped = True
            break
        if not stripped:
            out.append(text[i])
            i += 1
    return "".join(out)


def _strip_affiliation_blocks_for_image_extraction(text: str) -> str:
    r"""Remove ``\\affiliation[...]{...}`` blocks so institution logos are not raster indices.

    Templates such as *fairmeta* / NeurIPS-style put ``\\includegraphics`` inside
    ``\\affiliation`` lines. Those images are not numbered figures in ar5iv HTML; body
    figures still use ``x1.png``, ``xN`` in **figure** order. Counting affiliation
    logos shifts ``image_map[0]`` to e.g. ``unc_logo`` while the first HTML figure may
    reference ``x5`` (first float), breaking opaque-URL fallback pairing.
    """
    cmd = "\\affiliation"
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        stripped = False
        if text.startswith(cmd, i):
            j = i + len(cmd)
            if j < n and text[j].isalpha():
                pass  # longer command name
            else:
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] == "[":
                    depth = 1
                    j += 1
                    while j < n and depth > 0:
                        if text[j] == "[":
                            depth += 1
                        elif text[j] == "]":
                            depth -= 1
                        j += 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] == "{":
                    end = _find_matching_brace_end(text, j)
                    if end is not None:
                        out.append(" " * (end - i + 1))
                        i = end + 1
                        stripped = True
        if not stripped:
            out.append(text[i])
            i += 1
    return "".join(out)


def _extract_figure_env_text(text: str) -> str:
    r"""Return the concatenation of all ``\begin{figure}...\end{figure}`` bodies.

    ar5iv renames rasterized float figures to ``x1.png``, ``x2.png``, ... in
    **float-figure** order. ``\includegraphics`` that live outside a ``figure``
    env (inline images inside ``table``/``tabular``/multirow cells, or bare
    in-text graphics) keep their original names in the HTML and are *not*
    counted by the ``xN`` scheme. Counting them in TeX document order shifts
    ``image_map`` indices out of sync with HTML float numbers, so the
    positional fallback pairs each float caption with the wrong file
    (e.g. Figure 1's ``x1.png`` -> TeX index 0 = an inline table image).

    Only graphics inside ``figure``/``figure*`` envs are emitted. Nested envs
    (``tabular``, ``subfigure``, ``overpic``) within a figure are included.
    """
    env_re = re.compile(r"\\(begin|end)\s*\{([^}]+)\}")
    chunks: list[str] = []
    fig_depth = 0
    pos = 0
    for m in env_re.finditer(text):
        if fig_depth > 0 and m.start() > pos:
            chunks.append(text[pos : m.start()])
        kind, name = m.group(1), m.group(2).strip()
        is_figure = name in ("figure", "figure*")
        if kind == "begin" and is_figure:
            fig_depth += 1
        elif kind == "end" and is_figure and fig_depth > 0:
            fig_depth -= 1
        pos = m.end()
    return "\n".join(chunks)


def _parse_images_from_tex(tex_file: Path, base_dir: Path, all_images: list[Path]) -> dict[str, Path]:
    r"""Parse image references from LaTeX file in document order.

    Expands \\input/\\include recursively so images in included files are found.
    Returns ordered mapping (label -> path) covering **every** ``\includegraphics``
    so all referenced images get processed/copied (inline table images included).

    Graphics only used in the title block (``\\icmltitle``, ``\\title``) or author
    metadata (``\\affiliation{...}`` logos) are excluded so institution logos do
    not occupy processing slots.
    """
    expanded = _expand_tex_includes(tex_file, base_dir)
    expanded = _strip_title_blocks_for_image_extraction(expanded)
    expanded = _strip_affiliation_blocks_for_image_extraction(expanded)
    includegraphics_pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    # overpic environment: \begin{overpic}[options]{path}
    overpic_pattern = re.compile(r"\\begin\{overpic\}(?:\[[^\]]*\])?\{([^}]+)\}")
    image_map: dict[str, Path] = {}
    seen_paths: set[Path] = set()
    counter = 0

    def _process_match(match: re.Match[str]) -> None:
        nonlocal counter
        line_start = expanded.rfind("\n", 0, match.start()) + 1
        if expanded[line_start : match.start()].strip().startswith("%"):
            return
        image_path_str = match.group(1).strip()
        image_path = _resolve_image_path(image_path_str, base_dir, all_images)
        if image_path and image_path not in seen_paths:
            seen_paths.add(image_path)
            image_map[f"fig_{counter}"] = image_path
            counter += 1

    for match in includegraphics_pattern.finditer(expanded):
        _process_match(match)

    for match in overpic_pattern.finditer(expanded):
        _process_match(match)

    return image_map


def _parse_figure_env_images_from_tex(tex_file: Path, base_dir: Path, all_images: list[Path]) -> list[Path]:
    r"""Return ``\includegraphics`` paths that live inside ``\begin{figure}`` envs, in order.

    ar5iv renames rasterized float figures to ``x1.png``, ``x2.png``, ... in
    **float-figure** order. ``\includegraphics`` outside a ``figure`` env
    (inline images in ``table``/``tabular``/multirow cells, or bare in-text
    graphics) keep their original filenames in the HTML and are *not* part of
    the ``xN`` scheme. Counting them in document order shifts positional
    indices out of sync with HTML float numbers, so the resolver pairs each
    float caption with the wrong file (e.g. Figure 1's ``x1.png`` -> an inline
    table image).

    This list feeds the figure-index map only; every image still gets processed
    via :func:`_parse_images_from_tex`.
    """
    expanded = _expand_tex_includes(tex_file, base_dir)
    expanded = _strip_title_blocks_for_image_extraction(expanded)
    expanded = _strip_affiliation_blocks_for_image_extraction(expanded)
    figure_text = _extract_figure_env_text(expanded)
    includegraphics_pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    overpic_pattern = re.compile(r"\\begin\{overpic\}(?:\[[^\]]*\])?\{([^}]+)\}")
    ordered: list[Path] = []
    seen_paths: set[Path] = set()

    def _process_match(match: re.Match[str]) -> None:
        line_start = figure_text.rfind("\n", 0, match.start()) + 1
        if figure_text[line_start : match.start()].strip().startswith("%"):
            return
        image_path_str = match.group(1).strip()
        image_path = _resolve_image_path(image_path_str, base_dir, all_images)
        if image_path and image_path not in seen_paths:
            seen_paths.add(image_path)
            ordered.append(image_path)

    for match in includegraphics_pattern.finditer(figure_text):
        _process_match(match)
    for match in overpic_pattern.finditer(figure_text):
        _process_match(match)
    return ordered


def _resolve_image_path(image_path_str: str, base_dir: Path, all_images: list[Path]) -> Path | None:
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
