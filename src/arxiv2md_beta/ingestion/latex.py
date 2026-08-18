"""LaTeX ingestion pipeline for arXiv LaTeX -> Markdown with image support."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

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
    remove_inline_citations: bool = False,
    linked_citations: bool = False,
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
            remove_inline_citations=remove_inline_citations,
            linked_citations=linked_citations,
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
    remove_inline_citations: bool = False,
    linked_citations: bool = False,
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
    remove_inline_citations : bool
        Remove inline citations
    linked_citations : bool
        Render inline citations as [N](#ref-N) links
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
        from arxiv2md_beta.ir import LaTeXBuilder
        from arxiv2md_beta.ir.resolvers import ImageResolver
        from arxiv2md_beta.ir.transforms import build_default_pipeline
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
        pipeline = build_default_pipeline(
            parser="latex",
            section_filter_mode=section_filter_mode,
            selected_sections=sections,
            remove_refs=remove_refs,
            reference_section_titles=get_settings().ingestion.reference_section_titles,
        )
        pipeline.run(doc)
        return doc

    try:
        doc = await asyncio.to_thread(_build_latex_ir)
    except ParserNotAvailableError:
        raise
    except Exception as e:
        raise ParseError(f"Failed to parse LaTeX: {e}") from e

    # Shared finalize tail: split Markdown emission + paper.yml + structured export.
    from arxiv2md_beta.ingestion.ir_finalize import finalize_ingestion_output

    merge_tex_affiliations_if_configured(api_metadata, tex_source_info)
    result, metadata = finalize_ingestion_output(
        doc,
        arxiv_id=arxiv_id,
        version=version,
        paper_output_dir=paper_output_dir,
        paper_yml_data=dict(api_metadata),
        linked_citations=linked_citations,
        remove_inline_citations=remove_inline_citations,
        structured_output=structured_output,
        emit_graph_csv=emit_graph_csv,
        images_subdir=images_dir_name,
        extra_metadata={"submission_date": submission_date},
    )
    return result, metadata
