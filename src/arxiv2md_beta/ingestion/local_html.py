"""Local HTML file ingestion pipeline for processing saved HTML papers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from arxiv2md_beta.exceptions import IngestionError
from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.schemas import IngestionResult, LocalHtmlQuery
from arxiv2md_beta.settings import get_settings


class LocalHtmlIngestionError(IngestionError):
    """Raised when local HTML ingestion fails."""

    pass


async def ingest_local_html(
    query: LocalHtmlQuery,
    base_output_dir: Path,
    source: str = "Local",
    short: str | None = None,
    no_images: bool = False,
    remove_refs: bool = False,
    remove_inline_citations: bool = False,
    linked_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Process a local HTML file and convert to Markdown via the IR pipeline."""
    sections = sections or []

    # Read HTML content and parse off the event loop (BS4 parse is CPU-bound).
    try:
        from arxiv2md_beta.html.parser import parse_arxiv_html

        parsed = await asyncio.to_thread(
            lambda: parse_arxiv_html(query.html_path.read_text(encoding="utf-8", errors="ignore"))
        )
    except OSError as e:
        raise LocalHtmlIngestionError(f"Failed to read HTML file: {e}") from e
    except (ValueError, RuntimeError) as e:
        raise LocalHtmlIngestionError(f"Failed to parse HTML: {e}") from e

    # Use provided metadata or fall back to parsed
    title = parsed.title or query.title or query.html_path.stem
    submission_date = query.submission_date

    # Create paper-specific output directory
    from arxiv2md_beta.output.layout import create_paper_output_dir

    paper_output_dir = create_paper_output_dir(
        base_output_dir,
        submission_date,
        title,
        source=source or query.source,
        short=short,
    )
    images_dir_name = get_settings().cli_defaults.images_subdir
    images_dir = paper_output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)

    # Process associated files
    if not no_images:
        _copy_associated_files(query.html_path, images_dir)

    # Image resolver from copied files (name + stem → relative path).
    image_stem_map: dict[str, Path] = {}
    for img in images_dir.iterdir():
        if img.is_file():
            rel = Path(images_dir_name) / img.name
            image_stem_map[img.name] = rel
            image_stem_map[img.stem] = rel

    arxiv_id = query.html_path.stem

    # Build IR via HTMLBuilder (consumes ParsedArxivHtml, same as the orchestrator).
    def _build_ir() -> DocumentIR:
        from arxiv2md_beta.ingestion._builders import build_html_document

        return build_html_document(
            parsed,
            arxiv_id=arxiv_id,
            image_stem_map=image_stem_map,
            images_subdir=images_dir_name,
            section_filter_mode=section_filter_mode,
            sections=sections,
            remove_refs=remove_refs,
            paper_output_dir=paper_output_dir,
        )

    try:
        doc = await asyncio.to_thread(_build_ir)
    except Exception as e:
        raise LocalHtmlIngestionError(f"Failed to build IR: {e}") from e

    # Shared finalize tail: split Markdown emission + paper.yml + structured export.
    from arxiv2md_beta.ingestion.ir_finalize import finalize_ingestion_output

    return await asyncio.to_thread(
        finalize_ingestion_output,
        doc,
        arxiv_id=arxiv_id,
        paper_output_dir=paper_output_dir,
        paper_yml_data={
            "title": doc.metadata.title or title,
            "authors": [a.name for a in doc.metadata.authors],
            "abstract": doc.metadata.abstract_text,
            "submission_date": submission_date,
            "source": source or query.source,
            "html_path": str(query.html_path),
        },
        linked_citations=linked_citations,
        remove_inline_citations=remove_inline_citations,
        structured_output=structured_output,
        emit_graph_csv=emit_graph_csv,
        images_subdir=images_dir_name,
        extra_metadata={
            "submission_date": submission_date,
            "html_path": str(query.html_path),
        },
    )


def _copy_associated_files(html_path: Path, images_dir: Path) -> None:
    """Copy associated files from the HTML file's _files directory."""
    base_name = html_path.stem
    files_dir_patterns = [
        html_path.parent / f"{base_name}_files",
        html_path.parent / f"{base_name}.files",
        html_path.parent / f"{base_name}_resources",
    ]

    files_dir = None
    for pattern in files_dir_patterns:
        if pattern.exists() and pattern.is_dir():
            files_dir = pattern
            break

    if not files_dir:
        logger.debug(f"No associated files directory found for {html_path}")
        return

    from arxiv2md_beta.utils.file_copy import copy_images_flat

    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}
    sources = [img for ext in image_extensions for img in files_dir.rglob(f"*{ext}")]
    copied_count = copy_images_flat(sources, images_dir)

    if copied_count > 0:
        logger.info(f"Copied {copied_count} associated file(s) from {files_dir}")
