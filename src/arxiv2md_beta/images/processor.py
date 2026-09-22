"""Resolve and process images from TeX source."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import NamedTuple

from loguru import logger
from pdf2image import convert_from_path
from PIL import Image, ImageChops

from arxiv2md_beta.exceptions import ImageProcessingError, PDFConversionError
from arxiv2md_beta.latex.tex_source import TexSourceInfo
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.concurrency import concurrency_slot
from arxiv2md_beta.utils.progress import iterable_task_progress


def _init_pdf_worker() -> None:  # pragma: no cover - runs in worker processes
    """Pool initializer: lift PIL's decompression-bomb guard once per worker.

    arXiv sources are trusted; raising the limit per conversion (the previous
    approach) mutated a process-global around concurrent PIL calls.
    """
    Image.MAX_IMAGE_PIXELS = None


def _default_pdf_workers() -> int:
    cpus = os.cpu_count() or 2
    return min(4, max(1, cpus // 2))


_PROCESS_POOL: ProcessPoolExecutor | None = None


def _get_process_pool() -> ProcessPoolExecutor:
    """Lazily create the shared PDF→PNG process pool (reused across a batch)."""
    global _PROCESS_POOL
    if _PROCESS_POOL is None:
        configured = get_settings().images.pdf_workers
        workers = configured if configured > 0 else _default_pdf_workers()
        _PROCESS_POOL = ProcessPoolExecutor(max_workers=workers, initializer=_init_pdf_worker)
    return _PROCESS_POOL


def shutdown_process_pool() -> None:
    """Tear down the shared process pool (called once at CLI exit)."""
    global _PROCESS_POOL
    if _PROCESS_POOL is not None:
        _PROCESS_POOL.shutdown(wait=True)
        _PROCESS_POOL = None


def _compile_tex_figures(tex_source_info: TexSourceInfo, images_dir: Path) -> list[Path]:
    """Best-effort rasterization of TikZ/PGFPlots figure snippets."""
    main = tex_source_info.main_tex_file
    if main is None:
        return []
    source = main.read_text(encoding="utf-8", errors="replace")
    preamble = source.split(r"\begin{document}", 1)[0]
    snippets = sorted(tex_source_info.extracted_dir.rglob("*.tex"))
    snippets = [p for p in snippets if p != main and ("tikzpicture" in p.read_text(encoding="utf-8", errors="ignore"))]
    results: list[Path] = []
    pdflatex_available: bool | None = None  # probed once, then trusted
    for index, snippet in enumerate(snippets, 1):
        if pdflatex_available is False:
            break  # no point timing out on every remaining snippet
        with tempfile.TemporaryDirectory(prefix="arxiv2md-tikz-") as tmp:
            work = Path(tmp)
            wrapper = work / "figure.tex"
            rel = snippet.relative_to(tex_source_info.extracted_dir).as_posix()
            wrapper.write_text(
                preamble + "\n\\pagestyle{empty}\n\\begin{document}\n\\input{" + rel + "}\n\\end{document}\n",
                encoding="utf-8",
            )
            try:
                subprocess.run(
                    [
                        "pdflatex",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        "-output-directory",
                        str(work),
                        str(wrapper),
                    ],
                    cwd=tex_source_info.extracted_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                    check=True,
                )
                pdflatex_available = True
                png = images_dir / f"figure-{index}.png"
                subprocess.run(
                    [
                        "pdftocairo",
                        "-png",
                        "-singlefile",
                        "-r",
                        "180",
                        str(work / "figure.pdf"),
                        str(png.with_suffix("")),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=True,
                )
                if png.exists():
                    results.append(png)
            except FileNotFoundError:
                # pdflatex is not installed: fail fast instead of re-probing
                # (and re-waiting) for every snippet.
                pdflatex_available = False
                logger.warning("pdflatex not found; skipping TikZ figure compilation")
                break
            except (OSError, subprocess.SubprocessError):
                logger.warning(f"Failed to compile TeX figure: {snippet.name}")
    return results


def _trim_whitespace(img: Image.Image, tolerance: int = 100) -> Image.Image:
    """Trim surrounding whitespace/background from image.

    PDF-to-PNG conversion often produces extra blank margins because the PDF
    page size exceeds the figure content. This crops to the actual content
    bounding box.

    Parameters
    ----------
    img : Image.Image
        PIL image (typically from pdf2image)
    tolerance : int
        Subtract from diff to ignore compression artifacts (0-255).
        Higher values trim more aggressively.

    Returns:
    -------
    Image.Image
        Cropped image, or original if trim fails
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    bg = Image.new(img.mode, img.size, img.getpixel((0, 0)))
    diff = ImageChops.difference(img, bg)
    diff = ImageChops.add(diff, diff, 2.0, -tolerance)
    bbox = diff.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


class ProcessedImages(NamedTuple):
    """Processed images ready for Markdown."""

    image_map: dict[int, Path]  # figure_index -> relative_path
    images_dir: Path  # Directory containing processed images
    filename_map: dict[int, str]  # figure_index -> original_filename (for reference)
    # TeX source stem / output basename -> relative path (for HTML <img src> matching)
    stem_to_image_path: dict[str, Path]
    # TeX source file -> relative output path (exact identity; LaTeX label
    # mapping keys on this instead of positional indices, which are renumbered
    # in float-figure order and diverge when images fail or are inline).
    source_paths: dict[Path, Path] = {}
    # Source filenames that failed processing entirely (surfaces silent drops).
    # Immutable default: a NamedTuple class attribute would be shared.
    failed: tuple[str, ...] = ()


async def process_images_async(
    tex_source_info: TexSourceInfo,
    output_dir: Path,
    images_dir_name: str = "images",
    max_concurrency: int | None = None,
) -> ProcessedImages:
    """Asynchronously process images from TeX source for use in Markdown.

    PDF conversions are offloaded to a shared ``ProcessPoolExecutor``
    (CPU-bound, reused across papers), while raster copies are handled via
    ``asyncio.gather`` in a thread pool. A semaphore limits total concurrent
    tasks. TikZ figure compilation runs in a worker thread.

    Parameters
    ----------
    tex_source_info : TexSourceInfo
        Information about extracted TeX source
    output_dir : Path
        Directory where Markdown file will be saved
    images_dir_name : str
        Name of images subdirectory
    max_concurrency : int | None
        Maximum concurrent image tasks; defaults to ``settings.images.max_concurrency``

    Returns:
    -------
    ProcessedImages
        Mapping from figure index to relative image path
    """
    images_dir = output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)

    image_files = list(tex_source_info.image_files.values())
    if not image_files:
        image_files = tex_source_info.all_images

    if not image_files:
        # pdflatex/pdftocairo subprocesses are slow (up to ~45s each) — run
        # them off the event loop so concurrent batch papers keep progressing.
        compiled = await asyncio.to_thread(_compile_tex_figures, tex_source_info, images_dir)
        if compiled:
            relative = {i: p.relative_to(output_dir) for i, p in enumerate(compiled)}
            stem_map = {p.stem: p.relative_to(output_dir) for p in compiled}
            return ProcessedImages(relative, images_dir, {}, stem_map)
        logger.warning("No images found in TeX source")
        return ProcessedImages(image_map={}, images_dir=images_dir, filename_map={}, stem_to_image_path={})

    logger.info(f"Processing {len(image_files)} images...")

    img_cfg = get_settings().images
    disable_tqdm = img_cfg.disable_tqdm
    sem = asyncio.Semaphore(max(1, max_concurrency if max_concurrency is not None else img_cfg.max_concurrency))
    # Shared, lazily-created pool (see _get_process_pool) — reused across a
    # batch instead of fork/teardown per paper.
    process_pool = _get_process_pool()

    image_map: dict[int, Path] = {}
    filename_map: dict[int, str] = {}
    stem_to_image_path: dict[str, Path] = {}
    failed: list[str] = []
    source_paths: dict[Path, Path] = {}
    # source_path -> (relative_path, original_filename), used to rebuild the
    # figure-index map in float-figure order after concurrent processing.
    source_to_outcome: dict[Path, tuple[Path, str]] = {}

    assigned_names = _unique_output_names(image_files)

    async def _process_one(idx: int, source_path: Path) -> None:
        suffix = source_path.suffix.lower()
        is_pdf = suffix == ".pdf"
        async with sem:
            loop = asyncio.get_running_loop()
            # Use partial to pass keyword-only arguments to _process_single_image
            bound_func = partial(
                _process_single_image,
                assigned_name=assigned_names[source_path],
                dpi=img_cfg.pdf_to_png_dpi,
                trim_whitespace=img_cfg.trim_whitespace,
                trim_tolerance=img_cfg.trim_whitespace_tolerance,
            )
            try:
                async with concurrency_slot("images", img_cfg.global_max_concurrency):
                    if is_pdf:
                        async with concurrency_slot("pdf", img_cfg.global_pdf_concurrency):
                            relative_path, original_filename = await loop.run_in_executor(
                                process_pool, bound_func, source_path, images_dir, idx
                            )
                    else:
                        relative_path, original_filename = await loop.run_in_executor(
                            None, bound_func, source_path, images_dir, idx
                        )
                image_map[idx] = relative_path
                filename_map[idx] = original_filename
                source_paths[source_path] = relative_path
                source_to_outcome[source_path] = (relative_path, original_filename)
                stem_to_image_path[original_filename] = relative_path
                stem_to_image_path[relative_path.name] = relative_path
                if source_path.name != relative_path.name:
                    stem_to_image_path[source_path.name] = relative_path
            except ImageProcessingError as e:
                logger.error(f"Failed to process image {source_path}: {e}")
                failed.append(source_path.name)
            except (OSError, ValueError, TypeError, RuntimeError) as e:
                logger.error(f"Failed to process image {source_path}: {e}")
                failed.append(source_path.name)

    with iterable_task_progress(
        "Processing images",
        len(image_files),
        disable=disable_tqdm,
    ) as advance:
        tasks = []
        for idx, source_path in enumerate(image_files):
            task = asyncio.create_task(_process_one(idx, source_path))
            task.add_done_callback(lambda _f: advance())
            tasks.append(task)
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    # Rebuild the figure-index map in float-figure order so ar5iv xN.png names
    # (and positional figure_index fallback) resolve to the correct file.
    # ``image_map`` currently keys by document-order processing index, which
    # includes inline table images; only figure-env graphics should occupy the
    # 0..N-1 slots that HTML floats index into.
    figure_image_files = tex_source_info.figure_image_files
    if figure_image_files:
        ordered_map: dict[int, Path] = {}
        ordered_filenames: dict[int, str] = {}
        next_idx = 0
        for src in figure_image_files:
            outcome = source_to_outcome.get(src)
            if outcome is None:
                continue
            ordered_map[next_idx] = outcome[0]
            ordered_filenames[next_idx] = outcome[1]
            next_idx += 1
        if ordered_map:
            image_map = ordered_map
            filename_map = ordered_filenames

    if failed:
        logger.warning(f"{len(failed)} image(s) failed to process: {', '.join(failed)}")

    return ProcessedImages(
        image_map=image_map,
        images_dir=images_dir,
        filename_map=filename_map,
        stem_to_image_path=stem_to_image_path,
        source_paths=source_paths,
        failed=tuple(failed),
    )


def _unique_output_names(image_files: list[Path]) -> dict[Path, str]:
    """Pre-assign one output filename per source, collision-free.

    Two TeX subdirectories commonly ship figures with the same stem; deriving
    the output name per-worker made them race to write the same file (torn
    PNG under concurrency) and the last writer silently won the stem→image
    mapping, attaching the wrong figure (audit5 G3-1 — the local-archive path
    already had ``_1``/``_2`` disambiguation, this path did not).
    """
    owner: dict[str, Path] = {}
    assigned: dict[Path, str] = {}
    for source_path in image_files:
        suffix = source_path.suffix.lower()
        # PDF/EPS/PS convert to PNG; raster formats keep their full name
        base = f"{source_path.stem}.png" if suffix in {".pdf", ".eps", ".ps"} else source_path.name
        if base not in owner:
            owner[base] = source_path
            assigned[source_path] = base
            continue
        stem, ext = Path(base).stem, Path(base).suffix
        n = 1
        while f"{stem}_{n}{ext}" in owner:
            n += 1
        unique = f"{stem}_{n}{ext}"
        owner[unique] = source_path
        assigned[source_path] = unique
    return assigned


def _atomic_write(write, output_path: Path) -> None:
    """Write via a temp sibling + ``os.replace``.

    The output file is never seen half-written, even if two workers ever
    target the same name.
    """
    fd, tmp_name = tempfile.mkstemp(dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        write(tmp)
        os.replace(tmp, output_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _process_single_image(
    source_path: Path,
    output_dir: Path,
    index: int,
    *,
    assigned_name: str | None = None,
    dpi: int,
    trim_whitespace: bool,
    trim_tolerance: int,
) -> tuple[Path, str]:
    """Process a single image file.

    Converts PDF to PNG, copies other formats as-is.
    Preserves original filename (without extension for PDF->PNG conversion).

    Parameters
    ----------
    source_path : Path
        Source image file path
    output_dir : Path
        Output directory for processed images
    index : int
        Image index (for fallback naming)
    assigned_name : str, optional
        Pre-assigned collision-free output filename (audit5 G3-1); falls
        back to the stem-derived name when absent.

    Returns:
    -------
    tuple[Path, str]
        Relative path to processed image and original filename
    """
    suffix = source_path.suffix.lower()
    original_filename = source_path.stem  # Filename without extension
    # Pre-assigned name wins; the branches below only fill the default when
    # no collision-free name was handed in (audit5 G3-1).
    output_filename: str | None = assigned_name

    if suffix == ".pdf":
        # Convert PDF to PNG, but keep original filename
        output_filename = output_filename or f"{original_filename}.png"
        output_path = output_dir / output_filename

        try:
            # Image.MAX_IMAGE_PIXELS is lifted once per pool worker by the
            # initializer (_init_pdf_worker) — arXiv papers can have high-DPI
            # figures that trigger the default limit.
            # use_cropbox=True: use PDF cropbox instead of mediabox to avoid extra
            # whitespace (matches what PDF viewers show)
            images = convert_from_path(
                str(source_path),
                first_page=1,
                last_page=1,
                dpi=dpi,
                use_cropbox=True,
            )
            if images:
                pil_img = images[0]
                if trim_whitespace:
                    pil_img = _trim_whitespace(pil_img, tolerance=trim_tolerance)
                _atomic_write(lambda p: pil_img.save(p, "PNG"), output_path)
                logger.debug(f"Converted PDF to PNG: {source_path} -> {output_path}")
            else:
                raise PDFConversionError(f"Failed to extract image from PDF: {source_path}")
        except (OSError, PermissionError, ValueError) as e:
            raise PDFConversionError(f"Failed to convert PDF {source_path}: {e}") from e

    elif suffix in {".png", ".jpg", ".jpeg"}:
        # Copy image files as-is, keep original filename
        output_filename = output_filename or source_path.name
        output_path = output_dir / output_filename
        _atomic_write(lambda p: shutil.copy2(source_path, p), output_path)
        # Basename matches by design; paths differ (TeX tree -> paper images/)
        logger.debug(f"Copied raster to output dir: {source_path} -> {output_path}")

    elif suffix in {".eps", ".ps"}:
        # Convert EPS/PS to PNG, keep original filename
        output_filename = output_filename or f"{original_filename}.png"
        output_path = output_dir / output_filename

        try:
            # Try to open with PIL (requires ghostscript for EPS)
            img: Image.Image = Image.open(source_path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            if trim_whitespace:
                img = _trim_whitespace(img, tolerance=trim_tolerance)
            img.save(output_path, "PNG")
            logger.debug(f"Converted {suffix} to PNG: {source_path} -> {output_path}")
        except (OSError, PermissionError, ValueError) as e:
            logger.warning(f"Failed to convert {suffix} {source_path}, copying as-is: {e}")
            # Fallback: copy as-is
            output_filename = output_filename or source_path.name
            output_path = output_dir / output_filename
            _atomic_write(lambda p: shutil.copy2(source_path, p), output_path)
            logger.debug(f"Copied {suffix} as-is (fallback): {source_path} -> {output_path}")

    else:
        # Unknown format, copy as-is
        logger.warning(f"Unknown image format {suffix}, copying as-is")
        output_filename = output_filename or source_path.name
        output_path = output_dir / output_filename
        _atomic_write(lambda p: shutil.copy2(source_path, p), output_path)
        logger.debug(f"Copied unknown format as-is: {source_path} -> {output_path}")

    # Return relative path from output_dir's parent and original filename
    return Path(output_dir.name) / output_filename, original_filename


def build_latex_image_label_map(
    tex_source_info: TexSourceInfo,
    processed_images: ProcessedImages | None,
) -> dict[str, Path]:
    r"""Map LaTeX ``\includegraphics`` keys to processed image paths.

    Keys are the TeX label, the source filename, and the path relative to the
    extraction dir; values are the processed image paths (for ImageResolver).

    Lookup keys on the source file's identity
    (:attr:`ProcessedImages.source_paths`), never on positional indices — the
    public ``image_map`` is renumbered in float-figure order and would hand
    the wrong file to any label whose image is inline, failed, or merely
    ordered differently in the document.
    """
    latex_image_map: dict[str, Path] = {}
    if not processed_images:
        return latex_image_map
    for label, source_path in tex_source_info.image_files.items():
        out_path = processed_images.source_paths.get(source_path)
        if out_path is None:
            continue
        latex_image_map[label] = out_path
        latex_image_map[source_path.name] = out_path
        try:
            rel_path = source_path.relative_to(tex_source_info.extracted_dir)
            latex_image_map[str(rel_path)] = out_path
        except ValueError:
            pass
    return latex_image_map
