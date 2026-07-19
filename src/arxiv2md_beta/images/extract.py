"""Extract and process figures from arXiv TeX source only (no Markdown)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from arxiv2md_beta.images.processor import ProcessedImages, process_images_async
from arxiv2md_beta.latex.tex_source import fetch_and_extract_tex_source


async def extract_arxiv_images(
    *,
    arxiv_id: str,
    version: str | None,
    output_dir: Path,
    images_subdir: str,
    use_tex_cache: bool = True,
) -> ProcessedImages:
    r"""Download TeX source and run the same image pipeline as full LaTeX ingestion.

    Does not invoke pandoc or write Markdown. Intended for testing image extraction
    and PDF→PNG processing settings.
    """
    tex_source_info = await fetch_and_extract_tex_source(arxiv_id, version=version, use_cache=use_tex_cache)
    if not tex_source_info.main_tex_file:
        logger.warning("No main .tex file found; processing image files discovered in the extract.")
    return await process_images_async(tex_source_info, output_dir, images_subdir)

