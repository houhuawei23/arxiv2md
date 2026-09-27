"""Download and extract TeX source from arXiv or local archive."""

from __future__ import annotations

import asyncio
import gzip
import re
import shutil
import tarfile
import uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from loguru import logger

from arxiv2md_beta.exceptions import ImageProcessingError, NetworkError, StorageError
from arxiv2md_beta.network.download import (
    cache_dir_for,
    download_file_with_retries,
    is_cache_fresh,
    with_mirror_fallback,
)
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.text import find_matching_brace_end


class TexSourceInfo(NamedTuple):
    """Information about extracted TeX source."""

    extracted_dir: Path
    main_tex_file: Path | None
    image_files: dict[str, Path]  # figure_label -> local_path
    all_images: list[Path]  # All image files found
    # \includegraphics inside \begin{figure} envs only, in float order. Drives
    # the figure-index map so ar5iv xN.png names resolve to the right file.
    figure_image_files: list[Path] = []


def _info_paths_intact(info: TexSourceInfo) -> bool:
    """True when every path in *info* still resolves under ``extracted_dir``.

    A concurrent conversion can rmtree+rename the shared extract dir while
    another task is mid-read; that shows up as silently empty results (rglob
    on a missing dir yields nothing), never as an exception. Callers use this
    to detect the race and fall back to a fresh extraction.
    """
    paths = list(info.all_images) + list(info.image_files.values())
    if info.main_tex_file is not None:
        paths.append(info.main_tex_file)
    return all(p.exists() for p in paths)


def _rebase_tex_info(info: TexSourceInfo, old_root: Path, new_root: Path) -> TexSourceInfo:
    """Re-root *info* paths from ``old_root`` to ``new_root`` (same tree, renamed).

    Info is extracted from the private staging tree to avoid racing a
    concurrent swap; this retargets the resulting paths onto the canonical
    cache directory the rest of the pipeline expects.
    """

    def rebase(path: Path) -> Path:
        try:
            return new_root / path.relative_to(old_root)
        except ValueError:
            return path

    return TexSourceInfo(
        extracted_dir=new_root,
        main_tex_file=rebase(info.main_tex_file) if info.main_tex_file is not None else None,
        image_files={label: rebase(path) for label, path in info.image_files.items()},
        all_images=[rebase(path) for path in info.all_images],
        figure_image_files=[rebase(path) for path in info.figure_image_files],
    )


class TexSourceNotFoundError(NetworkError):
    """Raised when TeX source is not available (HTTP 404 or download exhausted)."""

    pass


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
    cache_dir = cache_dir_for(arxiv_id, version)
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
        and is_cache_fresh(tex_source_path)
    ):
        try:
            info = _extract_info_from_dir(extracted_dir)
            # A concurrent conversion may swap the shared extract dir between
            # the checks above and the reads — that manifests as silently
            # empty info, not an exception, so verify the paths still resolve.
            if _info_paths_intact(info):
                logger.info(f"Using cached TeX source for {arxiv_id}")
                _log_tex_source_paths(arxiv_id, cache_dir, extracted_dir, tex_source_path, info)
                return info
            logger.warning(f"Cached TeX extract for {arxiv_id} changed mid-read (concurrent conversion); re-extracting")
        except OSError:
            logger.warning(f"Cached TeX extract for {arxiv_id} vanished mid-read; re-extracting")

    # Download TeX source
    tex_url = get_settings().urls.arxiv_src_template.format(arxiv_id=arxiv_id)
    logger.info(f"Downloading TeX source from {tex_url}")

    # TexSourceNotFoundError (404 / PDF-only / budget exhausted) and any other
    # NetworkError propagate to the orchestrator, which degrades to a
    # no-images conversion instead of failing the paper.
    await with_mirror_fallback(
        tex_url,
        describe="TeX source download",
        fetch=lambda url: _download_tex_source(url, tex_source_path),
    )

    # Extract archive into a task-private temp dir, then move into place.
    # Two concurrent conversions of the same paper share the cache path; a
    # shared extract dir let one task's failure rmtree delete the other's
    # freshly extracted files mid-read (audit5 X8). Info extraction therefore
    # happens on the PRIVATE staging tree, before the swap — a concurrent
    # task's rmtree+rename of the shared path can no longer affect our reads.
    # Last-writer-wins on the shared cache is safe: both tasks extracted the
    # same tarball.
    logger.info(f"Extracting TeX source to {extracted_dir}")
    staging_dir = extracted_dir.with_name(f"{extracted_dir.name}.tmp-{uuid.uuid4().hex}")
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_extract_archive, tex_source_path, staging_dir)
        staging_info = await asyncio.to_thread(_extract_info_from_dir, staging_dir)

        def _swap_into_place() -> None:
            # rmtree of the previous extract plus the rename: directory-wide
            # sync IO, off the event loop like the extraction itself (audit5 X8).
            if extracted_dir.exists():
                shutil.rmtree(extracted_dir, ignore_errors=True)
            staging_dir.replace(extracted_dir)

        await asyncio.to_thread(_swap_into_place)
        # staging_dir no longer exists (renamed); re-root the staging-tree
        # info onto the canonical cache path — no second rglob pass needed.
        info = _rebase_tex_info(staging_info, staging_dir, extracted_dir)
    except Exception as e:
        # Remove only our own partial extract — never a shared directory.
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ImageExtractionError(f"Failed to extract TeX source: {e}") from e

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
    if use_cache and extracted_dir.exists() and is_cache_fresh(archive_path) and _has_tex_files(extracted_dir):
        logger.info(f"Using cached extraction for {archive_path.name}")
        return _extract_info_from_dir(extracted_dir)

    logger.info(f"Extracting local archive: {archive_path}")

    # Extract into a task-private staging dir, read info there, then swap into
    # place — same pattern as the remote TeX cache: a concurrent conversion of
    # the same archive (or a failure cleanup) must never be able to delete or
    # partially observe another task's shared extract (audit5 X8).
    staging_dir = extracted_dir.with_name(f"{extracted_dir.name}.tmp-{uuid.uuid4().hex}")
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        # Determine archive type and extract
        if archive_path.suffix.lower() == ".zip" or str(archive_path).lower().endswith(".zip"):
            _extract_zip_archive(archive_path, staging_dir)
        elif (
            archive_path.suffix.lower() in (".gz", ".tgz")
            or str(archive_path).lower().endswith(".tar.gz")
            or str(archive_path).lower().endswith(".tgz")
        ):
            _extract_tar_archive(archive_path, staging_dir)
        else:
            raise ArchiveExtractionError(f"Unsupported archive format: {archive_path.suffix}")

        # Extract images and find main tex file — on the private staging tree,
        # before the swap, so no shared-state race can affect the reads.
        staging_info = _extract_info_from_dir(staging_dir)
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir, ignore_errors=True)
        staging_dir.replace(extracted_dir)
        return _rebase_tex_info(staging_info, staging_dir, extracted_dir)
    except Exception as e:
        # Remove only our own partial extract — never a shared directory.
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ArchiveExtractionError(f"Failed to extract archive: {e}") from e


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
    """Download TeX source with retries, progress bar and PDF-only sniffing."""
    await download_file_with_retries(
        url,
        output_path,
        label=f"download TeX source {url}",
        progress_label="Downloading TeX source",
        not_found_error=lambda: TexSourceNotFoundError(
            f"TeX source not found at {url}. This paper may not have TeX source available.",
            status_code=404,
        ),
        pdf_only_error=lambda: TexSourceNotFoundError(
            f"TeX source not available for this paper (arXiv served a PDF from {url}); "
            "the paper was likely submitted as PDF-only."
        ),
        exhausted=lambda last_exc: TexSourceNotFoundError(
            f"Failed to download TeX source from {url}: {last_exc}",
            status_code=getattr(last_exc, "status_code", None),
        ),
    )


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


def _expand_tex_includes(tex_file: Path, base_dir: Path) -> str:
    r"""Expand ``\\input`` / ``\\include`` in *tex_file* (image/affiliation path).

    Delegates to the single resolver in :mod:`arxiv2md_beta.latex.includes`
    with the pandoc-only passes disabled — the old second implementation with
    its own recursion is gone (audit5 X2 unification).
    """
    from arxiv2md_beta.latex.includes import resolve_latex_includes

    return resolve_latex_includes(
        tex_file,
        base_dir,
        expand_lstinputlisting=False,
        expand_bibliography=False,
        fix_orphan_ends=False,
    )


# One expansion per (main tex, extract dir) serves the image parse, the
# figure-env parse and the author/affiliation parse (audit5 X2: it used to
# run three times). Keyed with the extract dir's mtime so a fresh extract
# re-expands; LRU-capped because expanded trees are megabyte-scale strings.
_EXPANDED_TEX_CACHE: OrderedDict[tuple[str, str, int], str] = OrderedDict()
_EXPANDED_TEX_CACHE_MAX = 8


def _expanded_tex_cached(tex_file: Path, base_dir: Path) -> str:
    """Memoized :func:`_expand_tex_includes`."""
    try:
        sig = base_dir.stat().st_mtime_ns
    except OSError:
        sig = 0
    key = (str(tex_file), str(base_dir), sig)
    hit = _EXPANDED_TEX_CACHE.get(key)
    if hit is not None:
        _EXPANDED_TEX_CACHE.move_to_end(key)
        return hit
    expanded = _expand_tex_includes(tex_file, base_dir)
    _EXPANDED_TEX_CACHE[key] = expanded
    while len(_EXPANDED_TEX_CACHE) > _EXPANDED_TEX_CACHE_MAX:
        _EXPANDED_TEX_CACHE.popitem(last=False)
    return expanded


def expand_tex_source_for_parsing(tex_source_info: TexSourceInfo) -> str:
    r"""Expand ``\\input`` / ``\\include`` from the main ``.tex`` for author/affiliation parsing."""
    if not tex_source_info.main_tex_file:
        return ""
    return _expanded_tex_cached(tex_source_info.main_tex_file, tex_source_info.extracted_dir)


# Longer command names share prefixes (e.g. \icmltitlerunning): the (?!…)
# lookahead excludes a following Unicode letter, matching the old isalpha check.
_TITLE_BLOCK_RE = re.compile(r"\\(?:icmltitle|title)(?![^\W\d_])", re.UNICODE)
_AFFILIATION_BLOCK_RE = re.compile(r"\\affiliation(?![^\W\d_])", re.UNICODE)


def _macro_block_brace_end(text: str, j: int) -> int | None:
    r"""Index of the ``}`` closing a ``\\cmd [..] {..}`` block whose name ends at *j*.

    Tolerates whitespace and one (nesting-aware) optional ``[..]`` group
    before the brace; None when the block is malformed/unterminated.
    """
    n = len(text)
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
        return None
    return find_matching_brace_end(text, j)


def _strip_macro_blocks(text: str, pattern: re.Pattern[str]) -> str:
    r"""Blank out every matched ``\\cmd [..] {..}`` block, preserving length.

    Replacement is same-length whitespace so downstream positional image
    mapping keeps its offsets. Matches nested inside an already-blanked
    block are dropped (the outer block wins), mirroring the old scanner.
    """
    spans: list[tuple[int, int]] = []
    for m in pattern.finditer(text):
        end = _macro_block_brace_end(text, m.end())
        if end is not None:
            spans.append((m.start(), end))
    if not spans:
        return text
    out: list[str] = []
    pos = 0
    for start, end in spans:
        if start < pos:
            continue  # nested inside the previous block
        out.append(text[pos:start])
        out.append(" " * (end - start + 1))
        pos = end + 1
    out.append(text[pos:])
    return "".join(out)


def _strip_title_blocks_for_image_extraction(text: str) -> str:
    r"""Remove ``\\icmltitle{...}`` and ``\\title{...}`` blocks from TeX for image ordering.

    ICML and similar templates often put a logo via ``\\includegraphics`` inside
    ``\\icmltitle{...}``. ar5iv HTML does not emit that graphic as ``Figure~1``; it
    renames body figures to ``x1.png``, ``x2.png``, … in **document** order. If we
    keep title graphics in the TeX raster list, ``image_map[0]`` is the logo while
    HTML ``x1`` is the first real figure (e.g. teaser), so positional fallback pairs
    the wrong file with each caption.
    """
    return _strip_macro_blocks(text, _TITLE_BLOCK_RE)


def _strip_affiliation_blocks_for_image_extraction(text: str) -> str:
    r"""Remove ``\\affiliation[...]{...}`` blocks so institution logos are not raster indices.

    Templates such as *fairmeta* / NeurIPS-style put ``\\includegraphics`` inside
    ``\\affiliation`` lines. Those images are not numbered figures in ar5iv HTML; body
    figures still use ``x1.png``, ``xN`` in **figure** order. Counting affiliation
    logos shifts ``image_map[0]`` to e.g. ``unc_logo`` while the first HTML figure may
    reference ``x5`` (first float), breaking opaque-URL fallback pairing.
    """
    return _strip_macro_blocks(text, _AFFILIATION_BLOCK_RE)


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
    expanded = _expanded_tex_cached(tex_file, base_dir)
    expanded = _strip_title_blocks_for_image_extraction(expanded)
    expanded = _strip_affiliation_blocks_for_image_extraction(expanded)
    index = _ImageIndex.build(all_images)
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
        image_path = _resolve_image_path(image_path_str, base_dir, index)
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
    expanded = _expanded_tex_cached(tex_file, base_dir)
    expanded = _strip_title_blocks_for_image_extraction(expanded)
    expanded = _strip_affiliation_blocks_for_image_extraction(expanded)
    figure_text = _extract_figure_env_text(expanded)
    index = _ImageIndex.build(all_images)
    includegraphics_pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
    overpic_pattern = re.compile(r"\\begin\{overpic\}(?:\[[^\]]*\])?\{([^}]+)\}")
    ordered: list[Path] = []
    seen_paths: set[Path] = set()

    def _process_match(match: re.Match[str]) -> None:
        line_start = figure_text.rfind("\n", 0, match.start()) + 1
        if figure_text[line_start : match.start()].strip().startswith("%"):
            return
        image_path_str = match.group(1).strip()
        image_path = _resolve_image_path(image_path_str, base_dir, index)
        if image_path and image_path not in seen_paths:
            seen_paths.add(image_path)
            ordered.append(image_path)

    for match in includegraphics_pattern.finditer(figure_text):
        _process_match(match)
    for match in overpic_pattern.finditer(figure_text):
        _process_match(match)
    return ordered


@dataclass(frozen=True)
class _ImageIndex:
    r"""Stem lookup tables over one extracted image set (audit5 X5).

    ``\includegraphics`` resolution used to linear-scan ``all_images`` up to
    twice per reference (exact stem, then case-folded) — O(graphics × images).
    The index is built once per parse; first-occurrence-wins keeps the old
    scan's precedence, and exact matches still beat case-folded ones.
    """

    paths: set[Path]
    exact: dict[str, Path]
    folded: dict[str, Path]

    @classmethod
    def build(cls, all_images: list[Path]) -> _ImageIndex:
        exact: dict[str, Path] = {}
        folded: dict[str, Path] = {}
        for p in all_images:
            exact.setdefault(p.stem, p)
            folded.setdefault(p.stem.lower(), p)
        return cls(set(all_images), exact, folded)


def _resolve_image_path(image_path_str: str, base_dir: Path, index: _ImageIndex) -> Path | None:
    """Resolve image path from LaTeX reference to actual file."""
    # Remove common LaTeX path prefixes
    image_path_str = image_path_str.strip()
    if image_path_str.startswith("./"):
        image_path_str = image_path_str[2:]

    # Try direct match
    candidate = base_dir / image_path_str
    if candidate.exists() and candidate in index.paths:
        return candidate

    # Try stem match (exact first, then case-insensitive — as before)
    base_name = Path(image_path_str).stem
    hit = index.exact.get(base_name)
    if hit is not None:
        return hit
    return index.folded.get(base_name.lower())


def _has_tex_files(extracted_dir: Path) -> bool:
    """True if extraction looks complete (at least one .tex file)."""
    return any(extracted_dir.rglob("*.tex"))
