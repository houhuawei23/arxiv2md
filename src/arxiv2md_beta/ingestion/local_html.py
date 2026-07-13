"""Local HTML file ingestion pipeline for processing saved HTML papers."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, cast

from loguru import logger

from arxiv2md_beta.exceptions import IngestionError
from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.schemas import IngestionResult, LocalHtmlQuery


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
    remove_toc: bool = False,
    remove_inline_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Process a local HTML file and convert to Markdown via the IR pipeline."""
    sections = sections or []

    # Read HTML content
    try:
        html_content = query.html_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        raise LocalHtmlIngestionError(f"Failed to read HTML file: {e}") from e

    # Parse with the arXiv HTML parser (same as remote HTML + local HTML archives).
    try:
        from arxiv2md_beta.html.parser import parse_arxiv_html

        parsed = parse_arxiv_html(html_content)
    except (ValueError, RuntimeError, OSError) as e:
        raise LocalHtmlIngestionError(f"Failed to parse HTML: {e}") from e

    # Use provided metadata or fall back to parsed
    title = parsed.title or query.title or query.html_path.stem
    authors = [a.name for a in parsed.authors] if parsed.authors else query.authors
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
    images_dir_name = "images"
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
        from arxiv2md_beta.ir import (
            AnchorPass,
            FigureReorderPass,
            HTMLBuilder,
            NumberingPass,
            PassPipeline,
            SectionFilterPass,
        )
        from arxiv2md_beta.ir.resolvers import ImageResolver
        from arxiv2md_beta.settings import get_settings

        doc = HTMLBuilder(image_resolver=ImageResolver(stem_map=image_stem_map)).build(parsed, arxiv_id=arxiv_id)
        pp = PassPipeline()
        pp.add(SectionFilterPass(mode=section_filter_mode, selected=sections))
        if remove_refs:
            pp.add(
                SectionFilterPass(
                    mode="exclude",
                    selected=get_settings().ingestion.reference_section_titles,
                )
            )
        pp.add(NumberingPass())
        pp.add(FigureReorderPass())
        pp.add(AnchorPass())
        pp.run(doc)
        return doc

    try:
        doc = await asyncio.to_thread(_build_ir)
    except Exception as e:
        raise LocalHtmlIngestionError(f"Failed to build IR: {e}") from e

    # Emit markdown with reference/appendix split (mirrors IR orchestrator).
    from arxiv2md_beta.ir import MarkdownEmitter, split_ir_sections
    from arxiv2md_beta.output.markdown_utils import (
        count_sections,
        create_sections_tree,
        format_markdown_output,
        format_token_count,
    )
    from arxiv2md_beta.settings import get_settings

    emitter = MarkdownEmitter()
    main_irs, ref_irs, app_irs = split_ir_sections(
        doc.sections, get_settings().ingestion.reference_section_titles
    )
    original_sections = doc.sections
    doc.sections = main_irs
    content = format_markdown_output(emitter.emit(doc))
    doc.sections = ref_irs
    ref_raw = emitter.emit(doc) if ref_irs else ""
    content_references = format_markdown_output(ref_raw) if ref_raw.strip() else None
    doc.sections = app_irs
    app_raw = emitter.emit(doc) if app_irs else ""
    content_appendix = format_markdown_output(app_raw) if app_raw.strip() else None
    doc.sections = original_sections

    m = doc.metadata
    result_title = m.title or title
    author_names = [a.name for a in m.authors] or (list(authors) if authors else [])
    summary_lines: list[str] = []
    if result_title:
        summary_lines.append(f"# Title: {result_title}")
    summary_lines.append(f"- ArXiv: {arxiv_id}")
    if author_names:
        summary_lines.append(f"- Authors: {', '.join(author_names)}")
    summary_lines.append(f"- Sections: {count_sections(cast('list[Any]', doc.sections))}")
    tree_lines = ["Sections:"]
    if m.abstract_text:
        tree_lines.append("Abstract")
    tree_lines.append(create_sections_tree(cast("list[Any]", doc.sections)))
    sections_tree = "\n".join(tree_lines)
    token_body = "\n".join(x for x in (content, content_references, content_appendix or "") if x)
    token_estimate = format_token_count(sections_tree + "\n" + token_body)
    if token_estimate:
        summary_lines.append(f"- Estimated tokens: {token_estimate}")
    result = IngestionResult(
        summary="\n".join(summary_lines),
        sections_tree=sections_tree,
        content=content,
        content_references=content_references,
        content_appendix=content_appendix,
    )

    # Save metadata
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        metadata_dict = {
            "title": result_title,
            "authors": author_names,
            "abstract": m.abstract_text,
            "submission_date": submission_date,
            "source": source or query.source,
            "html_path": str(query.html_path),
        }
        save_paper_metadata(metadata_dict, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    structured_export: dict[str, Any] = {}
    try:
        from arxiv2md_beta.ir.emitters.json_emitter import JsonEmitter, normalize_structured_mode

        sm = normalize_structured_mode(structured_output)
        if sm != "none":
            structured_export = JsonEmitter(mode=sm).write_bundle(
                doc,
                paper_output_dir,
                images_subdir=images_dir_name,
                emit_graph_csv=emit_graph_csv,
            )
    except Exception as e:
        logger.warning(f"Structured JSON export failed: {e}")

    metadata: dict[str, Any] = {
        "title": result_title,
        "authors": author_names,
        "abstract": m.abstract_text,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,
        "html_path": str(query.html_path),
        "arxiv_id": arxiv_id,
        "structured_export": structured_export,
    }

    return result, metadata


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

    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}

    copied_count = 0
    for ext in image_extensions:
        for img_file in files_dir.rglob(f"*{ext}"):
            try:
                dest_path = images_dir / img_file.name
                counter = 1
                original_dest = dest_path
                while dest_path.exists():
                    stem = original_dest.stem
                    suffix = original_dest.suffix
                    dest_path = images_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                shutil.copy2(img_file, dest_path)
                copied_count += 1
                logger.debug(f"Copied associated file: {img_file} -> {dest_path}")
            except Exception as e:
                logger.warning(f"Failed to copy file {img_file}: {e}")

    if copied_count > 0:
        logger.info(f"Copied {copied_count} associated file(s) from {files_dir}")
