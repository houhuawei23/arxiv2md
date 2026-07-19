"""LaTeX ingestion pipeline for arXiv LaTeX -> Markdown with image support."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from loguru import logger

from arxiv2md_beta.exceptions import ParseError, ParserNotAvailableError
from arxiv2md_beta.images.processor import process_images_async
from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.latex.tex_source import TexSourceNotFoundError, fetch_and_extract_tex_source
from arxiv2md_beta.network.arxiv_api import author_display_names_from_metadata, fetch_arxiv_metadata
from arxiv2md_beta.output.metadata_tex import merge_tex_affiliations_if_configured
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.timing import async_timed_operation


async def ingest_paper_latex(
    *,
    arxiv_id: str,
    version: str | None,
    base_output_dir: Path,
    remove_refs: bool = False,
    remove_toc: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    no_images: bool = False,
    source: str = "Arxiv",
    short: str | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
    use_cache: bool = True,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Fetch, parse, and serialize an arXiv paper from LaTeX source into Markdown."""
    async with async_timed_operation(f"ingest_paper_latex({arxiv_id})"):
        return await _ingest_paper_latex_impl(
            arxiv_id=arxiv_id,
            version=version,
            base_output_dir=base_output_dir,
            remove_refs=remove_refs,
            remove_toc=remove_toc,
            section_filter_mode=section_filter_mode,
            sections=sections or [],
            no_images=no_images,
            source=source,
            short=short,
            structured_output=structured_output,
            emit_graph_csv=emit_graph_csv,
            use_cache=use_cache,
        )


async def _ingest_paper_latex_impl(
    *,
    arxiv_id: str,
    version: str | None,
    base_output_dir: Path,
    remove_refs: bool = False,
    remove_toc: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    no_images: bool = False,
    source: str = "Arxiv",
    short: str | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
    use_cache: bool = True,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Fetch, parse, and serialize an arXiv paper from LaTeX source into Markdown.

    Parameters
    ----------
    arxiv_id : str
        arXiv ID
    version : str | None
        Version string (e.g., "v1")
    base_output_dir : Path
        Base output directory (paper-specific directory will be created inside)
    remove_refs : bool
        Remove bibliography sections
    remove_toc : bool
        Remove table of contents
    section_filter_mode : str
        "include" or "exclude"
    sections : list[str] | None
        Section titles to filter
    no_images : bool
        If True, skip image downloading and processing

    Returns:
    -------
    tuple[IngestionResult, dict]
        Ingestion result and metadata

    Raises:
    ------
    TexSourceNotFoundError
        If TeX source is not available
    ParserNotAvailableError
        If pypandoc is not available
    """
    # Fetch metadata from API
    api_metadata = await fetch_arxiv_metadata(arxiv_id)
    fallback_title = get_settings().ingestion.latex_fallback_title
    title = api_metadata.get("title") or fallback_title
    submission_date = api_metadata.get("submission_date")

    # Create paper-specific output directory
    from arxiv2md_beta.output.layout import create_paper_output_dir

    paper_output_dir = create_paper_output_dir(
        base_output_dir, cast("str | None", submission_date), cast("str | None", title), source=source, short=short
    )
    images_dir_name = get_settings().cli_defaults.images_subdir

    # Fetch and extract TeX source
    tex_source_info = await fetch_and_extract_tex_source(arxiv_id, version=version, use_cache=use_cache)

    if not tex_source_info.main_tex_file:
        raise TexSourceNotFoundError(f"No main LaTeX file found for {arxiv_id}")

    # Process images if enabled
    processed_images = None
    if not no_images:
        processed_images = await process_images_async(tex_source_info, paper_output_dir, images_dir_name)

    # Build image map from LaTeX labels/paths to local paths
    # The image_map from tex_source_info uses labels, we need to map them to processed images
    latex_image_map: dict[str, Path] = {}
    if processed_images:
        for idx, (label, source_path) in enumerate(tex_source_info.image_files.items()):
            if idx in processed_images.image_map:
                latex_image_map[label] = processed_images.image_map[idx]
                # Also map by filename
                latex_image_map[source_path.name] = processed_images.image_map[idx]
                # Map by path relative to base_dir
                try:
                    rel_path = source_path.relative_to(tex_source_info.extracted_dir)
                    latex_image_map[str(rel_path)] = processed_images.image_map[idx]
                except ValueError:
                    pass

    # Build IR from LaTeX via Pandoc AST (offload blocking pandoc call to thread).
    # Replaces the legacy parse_latex_to_markdown + format_paper + latex/structured
    # chain with the unified IR pipeline (LaTeXBuilder → PassPipeline → MarkdownEmitter
    # + JsonEmitter), matching the HTML IR orchestrator.
    display_author_names = author_display_names_from_metadata(api_metadata)
    abstract_text = cast("str | None", api_metadata.get("summary"))

    def _build_latex_ir() -> DocumentIR:
        from arxiv2md_beta.ir import (
            AnchorPass,
            FigureReorderPass,
            LaTeXBuilder,
            NumberingPass,
            PassPipeline,
            SectionFilterPass,
            SectionNumberingPass,
        )
        from arxiv2md_beta.ir.resolvers import ImageResolver
        from arxiv2md_beta.latex.includes import resolve_latex_includes

        main_tex = tex_source_info.main_tex_file
        assert main_tex is not None  # checked above; narrow for type-checker
        tex_content = resolve_latex_includes(
            main_tex,
            tex_source_info.extracted_dir,
        )
        resolver = ImageResolver(path_map=latex_image_map)
        doc = LaTeXBuilder(image_resolver=resolver).build(
            tex_content,
            arxiv_id=arxiv_id,
            title=title,
            authors=display_author_names or None,
            abstract=abstract_text,
            base_dir=tex_source_info.extracted_dir,
        )
        # Section filtering (replaces legacy filter_sections on SectionNode).
        pp = PassPipeline()
        pp.add(SectionFilterPass(mode=section_filter_mode, selected=sections or []))
        if remove_refs:
            pp.add(
                SectionFilterPass(
                    mode="exclude",
                    selected=get_settings().ingestion.reference_section_titles,
                )
            )
        pp.add(NumberingPass())
        pp.add(SectionNumberingPass())
        pp.add(FigureReorderPass())
        pp.add(AnchorPass())
        pp.run(doc)
        return doc

    try:
        doc = await asyncio.to_thread(_build_latex_ir)
    except ParserNotAvailableError:
        raise
    except Exception as e:
        raise ParseError(f"Failed to parse LaTeX: {e}") from e

    # Emit markdown with reference/appendix split (mirrors IR orchestrator).
    from arxiv2md_beta.ir import MarkdownEmitter, split_ir_sections
    from arxiv2md_beta.output.markdown_utils import (
        count_sections,
        create_sections_tree,
        format_markdown_output,
        format_token_count,
    )

    emitter = MarkdownEmitter()
    main_irs, ref_irs, app_irs = split_ir_sections(doc.sections, get_settings().ingestion.reference_section_titles)
    original_sections = doc.sections
    original_abstract = doc.abstract
    doc.sections = main_irs
    content = format_markdown_output(emitter.emit(doc))
    doc.sections = ref_irs
    doc.abstract = []  # suppress abstract in sidecars
    ref_raw = emitter.emit(doc) if ref_irs else ""
    content_references = format_markdown_output(ref_raw) if ref_raw.strip() else None
    doc.sections = app_irs
    app_raw = emitter.emit(doc) if app_irs else ""
    content_appendix = format_markdown_output(app_raw) if app_raw.strip() else None
    doc.sections = original_sections
    doc.abstract = original_abstract

    # Build IngestionResult summary + tree from IR sections (duck-typed: SectionIR
    # exposes .title/.children just like SectionNode).
    m = doc.metadata
    result_title = m.title or title
    author_names = [a.name for a in m.authors] or display_author_names
    summary_lines: list[str] = []
    if result_title:
        summary_lines.append(f"# Title: {result_title}")
    summary_lines.append(f"- ArXiv: {arxiv_id}")
    if version:
        summary_lines.append(f"- Version: {version}")
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

    # Save paper metadata to paper.yml
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        merge_tex_affiliations_if_configured(api_metadata, tex_source_info)
        save_paper_metadata(api_metadata, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    structured_export: dict[str, object] = {}
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

    metadata = {
        "title": result_title,
        "authors": author_names,
        "abstract": m.abstract_text,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,  # Return the directory path
        "arxiv_id": arxiv_id,
        "structured_export": structured_export,
    }

    return result, metadata
