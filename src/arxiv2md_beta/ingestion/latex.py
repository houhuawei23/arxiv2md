"""LaTeX ingestion pipeline for arXiv LaTeX -> Markdown with image support."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from arxiv2md_beta.html.sections import filter_sections
from arxiv2md_beta.images.resolver import process_images_async
from arxiv2md_beta.latex.parser import (
    ParserNotAvailableError,
    _enhance_section_markdown,
    parse_latex_to_markdown,
)
from arxiv2md_beta.latex.tex_source import TexSourceNotFoundError, fetch_and_extract_tex_source
from arxiv2md_beta.network.arxiv_api import author_display_names_from_metadata, fetch_arxiv_metadata
from arxiv2md_beta.output.formatter import format_paper
from arxiv2md_beta.output.metadata_tex import merge_tex_affiliations_if_configured
from arxiv2md_beta.schemas import IngestionResult, SectionNode
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.metrics import async_timed_operation


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
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
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
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
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
        base_output_dir, submission_date, title, source=source, short=short
    )
    images_dir_name = get_settings().cli_defaults.images_subdir

    # Fetch and extract TeX source
    tex_source_info = await fetch_and_extract_tex_source(arxiv_id, version=version)

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

    # Parse LaTeX to Markdown
    try:
        parsed_latex = parse_latex_to_markdown(
            tex_source_info.main_tex_file,
            tex_source_info.extracted_dir,
            latex_image_map,
        )
    except ParserNotAvailableError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to parse LaTeX: {e}") from e

    # Get sections from parsed LaTeX (new structured parsing)
    sections = parsed_latex.sections or []
    if not sections:
        # Fallback: create a single section with the full content
        sections = [
            SectionNode(
                title=parsed_latex.title or fallback_title,
                level=1,
                anchor=None,
                html=None,
                markdown=parsed_latex.markdown,
                children=[],
            )
        ]

    # Apply section filtering
    sections_to_use = filter_sections(
        sections,
        mode=section_filter_mode,
        selected=sections or [],
    )

    # Remove refs if requested
    if remove_refs:
        ing = get_settings().ingestion
        sections_to_use = filter_sections(
            sections_to_use,
            mode="exclude",
            selected=ing.reference_section_titles,
        )

    # Enhance section markdown with anchors
    _enhance_section_markdown(sections_to_use)

    display_author_names = author_display_names_from_metadata(api_metadata) or list(parsed_latex.authors or [])

    # Format output with file splitting and TOC support
    result = format_paper(
        arxiv_id=arxiv_id,
        version=version,
        title=parsed_latex.title,
        authors=display_author_names,
        abstract=parsed_latex.abstract,
        sections=sections_to_use,
        include_toc=not remove_toc,  # Enable TOC generation
        include_abstract_in_tree=parsed_latex.abstract is not None,
        split_for_reference=True,  # Enable file splitting
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
        from arxiv2md_beta.latex.structured import (
            extract_abstract_blocks,
            extract_blocks_from_sections,
            write_structured_bundle_for_latex,
        )
        from arxiv2md_beta.output.structured_export import (
            normalize_structured_mode,
        )

        sm = normalize_structured_mode(structured_output)
        if sm != "none":
            stem_map = processed_images.stem_to_image_path if processed_images else None
            img_map = processed_images.image_map if processed_images else None

            # Extract blocks from sections for richer structured output
            body_blocks = extract_blocks_from_sections(sections_to_use)
            abstract_blocks = extract_abstract_blocks(parsed_latex.abstract)

            structured_export = write_structured_bundle_for_latex(
                paper_output_dir=paper_output_dir,
                mode=sm,
                emit_graph_csv=emit_graph_csv,
                arxiv_id=arxiv_id,
                arxiv_version=version,
                title=parsed_latex.title or title,
                authors=list(parsed_latex.authors or []),
                submission_date=submission_date,
                parser="latex",
                sections=sections_to_use,
                abstract_blocks=abstract_blocks,
                body_blocks=body_blocks,
                abstract_md=parsed_latex.abstract,
                stem_to_image_path=stem_map,
                image_map=img_map,
                images_subdir=images_dir_name,
            )
    except Exception as e:
        logger.warning(f"Structured JSON export failed: {e}")

    metadata = {
        "title": parsed_latex.title or title,
        "authors": parsed_latex.authors,
        "abstract": parsed_latex.abstract,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,  # Return the directory path
        "arxiv_id": arxiv_id,
        "structured_export": structured_export,
    }

    return result, metadata

