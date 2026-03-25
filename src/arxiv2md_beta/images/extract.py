"""Extract and process figures from arXiv TeX source only (no Markdown)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from arxiv2md_beta.images.resolver import ProcessedImages, process_images
from arxiv2md_beta.latex.tex_source import fetch_and_extract_tex_source


async def extract_arxiv_images(
    *,
    arxiv_id: str,
    version: str | None,
    output_dir: Path,
    images_subdir: str,
    use_tex_cache: bool = True,
) -> ProcessedImages:
    """Download TeX source and run the same image pipeline as full LaTeX ingestion.

    Does not invoke pandoc or write Markdown. Intended for testing image extraction
    and PDF→PNG processing settings.

    Parameters
    ----------
    arxiv_id : str
        Normalized arXiv identifier (base id, version passed separately).
    version : str | None
        Version suffix (e.g. ``v1``) or None for latest in id string semantics.
    output_dir : Path
        Directory under which ``images_subdir`` will be created.
    images_subdir : str
        Subdirectory name for processed images (e.g. ``\"images\"``).
    use_tex_cache : bool
        If True, reuse cached TeX extract when fresh (same as full pipeline).

    Returns
    -------
    ProcessedImages
        Maps and absolute ``images_dir`` path.
    """
    tex_source_info = await fetch_and_extract_tex_source(
        arxiv_id, version=version, use_cache=use_tex_cache
    )
    if not tex_source_info.main_tex_file:
        logger.warning("No main .tex file found; processing image files discovered in the extract.")
    return process_images(tex_source_info, output_dir, images_subdir)
