"""Resolve and process images from TeX source."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple

from loguru import logger
from pdf2image import convert_from_path
from PIL import Image, ImageChops

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.progress import iterable_task_progress
from arxiv2md_beta.latex.tex_source import TexSourceInfo


class ImageProcessingError(Exception):
    """Raised when image processing fails."""

    pass


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

    Returns
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


def process_images(
    tex_source_info: TexSourceInfo,
    output_dir: Path,
    images_dir_name: str = "images",
) -> ProcessedImages:
    """Process images from TeX source for use in Markdown.

    Parameters
    ----------
    tex_source_info : TexSourceInfo
        Information about extracted TeX source
    output_dir : Path
        Directory where Markdown file will be saved
    images_dir_name : str
        Name of images subdirectory

    Returns
    -------
    ProcessedImages
        Mapping from figure index to relative image path
    """
    images_dir = output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)

    # Get all images in order (from image_files dict or all_images list)
    image_files = list(tex_source_info.image_files.values())
    if not image_files:
        # Fallback to all_images if no mapping found
        image_files = tex_source_info.all_images

    if not image_files:
        logger.warning("No images found in TeX source")
        return ProcessedImages(
            image_map={}, images_dir=images_dir, filename_map={}, stem_to_image_path={}
        )

    logger.info(f"Processing {len(image_files)} images...")

    img_cfg = get_settings().images
    disable_tqdm = img_cfg.disable_tqdm

    image_map: dict[int, Path] = {}
    filename_map: dict[int, str] = {}
    stem_to_image_path: dict[str, Path] = {}
    with iterable_task_progress(
        "Processing images",
        len(image_files),
        disable=disable_tqdm,
    ) as advance:
        for idx, source_image_path in enumerate(image_files):
            try:
                relative_path, original_filename = _process_single_image(
                    source_image_path,
                    images_dir,
                    idx,
                    dpi=img_cfg.pdf_to_png_dpi,
                    trim_whitespace=img_cfg.trim_whitespace,
                    trim_tolerance=img_cfg.trim_whitespace_tolerance,
                )
                image_map[idx] = relative_path
                filename_map[idx] = original_filename
                # HTML figure order often differs from \includegraphics order; match by name.
                stem_to_image_path[original_filename] = relative_path
                stem_to_image_path[relative_path.name] = relative_path
            except Exception as e:
                logger.error(f"Failed to process image {source_image_path}: {e}")
                # Continue with other images
            advance()

    return ProcessedImages(
        image_map=image_map,
        images_dir=images_dir,
        filename_map=filename_map,
        stem_to_image_path=stem_to_image_path,
    )


def _process_single_image(
    source_path: Path,
    output_dir: Path,
    index: int,
    *,
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

    Returns
    -------
    tuple[Path, str]
        Relative path to processed image and original filename
    """
    suffix = source_path.suffix.lower()
    original_filename = source_path.stem  # Filename without extension

    if suffix == ".pdf":
        # Convert PDF to PNG, but keep original filename
        output_filename = f"{original_filename}.png"
        output_path = output_dir / output_filename

        try:
            # Temporarily raise PIL's decompression limit for large-but-legitimate PDFs
            # (arXiv papers can have high-DPI figures that trigger the default limit)
            _max_pixels = getattr(Image, "MAX_IMAGE_PIXELS", None)
            try:
                Image.MAX_IMAGE_PIXELS = None  # Disable limit for trusted arXiv sources
                # Use lower DPI (150) for large PDFs to reduce memory; default is 200
                # use_cropbox=True: use PDF cropbox instead of mediabox to avoid extra
                # whitespace (matches what PDF viewers show)
                images = convert_from_path(
                    str(source_path),
                    first_page=1,
                    last_page=1,
                    dpi=dpi,
                    use_cropbox=True,
                )
            finally:
                if _max_pixels is not None:
                    Image.MAX_IMAGE_PIXELS = _max_pixels
            if images:
                pil_img = images[0]
                if trim_whitespace:
                    pil_img = _trim_whitespace(pil_img, tolerance=trim_tolerance)
                pil_img.save(output_path, "PNG")
                logger.debug(f"Converted PDF to PNG: {source_path} -> {output_path}")
            else:
                raise ImageProcessingError(f"Failed to extract image from PDF: {source_path}")
        except Exception as e:
            raise ImageProcessingError(f"Failed to convert PDF {source_path}: {e}") from e

    elif suffix in {".png", ".jpg", ".jpeg"}:
        # Copy image files as-is, keep original filename
        output_filename = source_path.name
        output_path = output_dir / output_filename
        shutil.copy2(source_path, output_path)
        # Basename matches by design; paths differ (TeX tree -> paper images/)
        logger.debug(f"Copied raster to output dir: {source_path} -> {output_path}")

    elif suffix in {".eps", ".ps"}:
        # Convert EPS/PS to PNG, keep original filename
        output_filename = f"{original_filename}.png"
        output_path = output_dir / output_filename

        try:
            # Try to open with PIL (requires ghostscript for EPS)
            img = Image.open(source_path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            if trim_whitespace:
                img = _trim_whitespace(img, tolerance=trim_tolerance)
            img.save(output_path, "PNG")
            logger.debug(f"Converted {suffix} to PNG: {source_path} -> {output_path}")
        except Exception as e:
            logger.warning(f"Failed to convert {suffix} {source_path}, copying as-is: {e}")
            # Fallback: copy as-is
            output_filename = source_path.name
            output_path = output_dir / output_filename
            shutil.copy2(source_path, output_path)
            logger.debug(f"Copied {suffix} as-is (fallback): {source_path} -> {output_path}")

    else:
        # Unknown format, copy as-is
        logger.warning(f"Unknown image format {suffix}, copying as-is")
        output_filename = source_path.name
        output_path = output_dir / output_filename
        shutil.copy2(source_path, output_path)
        logger.debug(f"Copied unknown format as-is: {source_path} -> {output_path}")

    # Return relative path from output_dir's parent and original filename
    return Path(output_dir.name) / output_filename, original_filename
