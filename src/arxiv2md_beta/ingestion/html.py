"""HTML ingestion pipeline for arXiv HTML -> Markdown with image support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arxiv2md_beta.network.arxiv_api import (
    author_display_names_from_metadata,
    fetch_arxiv_metadata,
    fill_arxiv_metadata_defaults,
)
from arxiv2md_beta.network.fetch import fetch_arxiv_html
from arxiv2md_beta.html.parser import parse_arxiv_html
from arxiv2md_beta.images.resolver import process_images
from arxiv2md_beta.html.markdown import convert_fragment_to_markdown
from arxiv2md_beta.output.formatter import format_paper
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.html.sections import filter_sections
from arxiv2md_beta.latex.tex_source import TexSourceNotFoundError, fetch_and_extract_tex_source


async def ingest_paper_html(
    *,
    arxiv_id: str,
    version: str | None,
    html_url: str,
    ar5iv_url: str | None = None,
    remove_refs: bool,
    remove_toc: bool,
    remove_inline_citations: bool = False,
    section_filter_mode: str,
    sections: list[str],
    base_output_dir: Path,
    no_images: bool = False,
    source: str = "Arxiv",
    short: str | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
    """Fetch, parse, and serialize an arXiv paper into Markdown with image support.

    Parameters
    ----------
    arxiv_id : str
        arXiv ID
    version : str | None
        Version string (e.g., "v1")
    html_url : str
        URL to HTML version
    ar5iv_url : str | None
        Fallback URL to ar5iv
    remove_refs : bool
        Remove bibliography sections
    remove_toc : bool
        Remove table of contents
    remove_inline_citations : bool
        Remove inline citations
    section_filter_mode : str
        "include" or "exclude"
    sections : list[str]
        Section titles to filter
    base_output_dir : Path
        Base output directory (paper-specific directory will be created inside)
    no_images : bool
        If True, skip image downloading and processing

    Returns
    -------
    tuple[IngestionResult, dict]
        Ingestion result and metadata
    """
    html = await fetch_arxiv_html(
        html_url, arxiv_id=arxiv_id, version=version, use_cache=True, ar5iv_url=ar5iv_url
    )
    parsed = parse_arxiv_html(html)
    ing = get_settings().ingestion

    # Fetch metadata from API to get submission date (more reliable)
    api_metadata = await fetch_arxiv_metadata(arxiv_id)
    # Summary / sidecars: use Atom API author names (order + spelling), not HTML ltx_authors
    # (HTML mixes affiliation lines like "Google Brain" into the author list).
    display_author_names = author_display_names_from_metadata(api_metadata) or list(parsed.authors)
    # Use API date if available, otherwise use parsed date
    submission_date = api_metadata.get("submission_date") or parsed.submission_date
    # Use API title if parsed title is None
    if not parsed.title and api_metadata.get("title"):
        parsed.title = api_metadata["title"]

    filtered_sections = filter_sections(parsed.sections, mode=section_filter_mode, selected=sections)
    if remove_refs:
        filtered_sections = filter_sections(
            filtered_sections, mode="exclude", selected=ing.reference_section_titles
        )

    # Check if abstract should be included based on section filter
    abstract_key = ing.abstract_section_title.lower()
    selected_lower = [s.lower() for s in sections]
    if section_filter_mode == "exclude":
        include_abstract = abstract_key not in selected_lower
    else:  # include mode
        include_abstract = not sections or abstract_key in selected_lower

    # Create paper-specific output directory
    from arxiv2md_beta.output.layout import create_paper_output_dir
    paper_output_dir = create_paper_output_dir(
        base_output_dir, submission_date, parsed.title, source=source, short=short
    )
    images_dir_name = get_settings().cli_defaults.images_subdir
    images_dir = paper_output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)
    
    # Process images if enabled
    image_map: dict[int, Path] | None = None
    image_stem_map: dict[str, Path] | None = None
    if not no_images:
        try:
            tex_source_info = await fetch_and_extract_tex_source(arxiv_id, version=version)
            processed_images = process_images(tex_source_info, paper_output_dir, images_dir_name)
            image_map = processed_images.image_map
            image_stem_map = processed_images.stem_to_image_path
        except TexSourceNotFoundError:
            # Continue without images if TeX source not available
            pass
        except Exception as e:
            # Log error but continue
            from loguru import logger
            logger.warning(f"Failed to process images: {e}")

    # Populate markdown with image map (shared figure_counter across abstract + sections)
    figure_counter: list[int] = [0]
    abstract_md: str | None = None
    if include_abstract:
        if parsed.abstract_html:
            # Convert abstract HTML to markdown (with figures when image_map available)
            abstract_md = convert_fragment_to_markdown(
                parsed.abstract_html,
                remove_inline_citations=remove_inline_citations,
                image_map=image_map,
                image_stem_map=image_stem_map,
                figure_counter=figure_counter,
                images_dir=images_dir,
            )
        else:
            abstract_md = parsed.abstract
    if parsed.front_matter_html:
        front_md = convert_fragment_to_markdown(
            parsed.front_matter_html,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )
        if front_md and include_abstract:
            abstract_md = (abstract_md or "") + ("\n\n" + front_md if abstract_md else front_md)
    for section in filtered_sections:
        _populate_section_markdown(
            section,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )

    result = format_paper(
        arxiv_id=arxiv_id,
        version=version,
        title=parsed.title,
        authors=display_author_names,
        abstract=abstract_md if include_abstract else None,
        sections=filtered_sections,
        include_toc=not remove_toc,
        include_abstract_in_tree=parsed.abstract is not None,
        split_for_reference=True,
    )

    # Save paper metadata to paper.yml (merge HTML when Atom API failed, e.g. 429)
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        base_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
        paper_meta = dict(api_metadata)
        if not paper_meta.get("title") and parsed.title:
            paper_meta["title"] = parsed.title
        if not paper_meta.get("summary") and parsed.abstract:
            paper_meta["summary"] = parsed.abstract
        if not paper_meta.get("authors") and parsed.authors:
            paper_meta["authors"] = [{"name": a} for a in parsed.authors if a]
        paper_meta = fill_arxiv_metadata_defaults(paper_meta, base_id)
        save_paper_metadata(paper_meta, paper_output_dir)
    except Exception as e:
        from loguru import logger
        logger.warning(f"Failed to save paper.yml: {e}")

    structured_export: dict[str, Any] = {}
    try:
        from arxiv2md_beta.output.structured_export import (
            normalize_structured_mode,
            write_structured_bundle,
        )

        sm = normalize_structured_mode(structured_output)
        if sm != "none":
            structured_export = write_structured_bundle(
                paper_output_dir=paper_output_dir,
                mode=sm,
                emit_graph_csv=emit_graph_csv,
                arxiv_id=arxiv_id,
                arxiv_version=version,
                title=parsed.title,
                authors=list(display_author_names or []),
                submission_date=submission_date,
                html_url=html_url,
                ar5iv_url=ar5iv_url,
                parser="html",
                sections=filtered_sections,
                abstract_md=abstract_md if include_abstract else None,
                abstract_html=parsed.abstract_html,
                front_matter_html=parsed.front_matter_html,
                include_abstract_parts=include_abstract,
                image_map=image_map,
                stem_to_image_path=image_stem_map,
                images_subdir=images_dir_name,
            )
    except Exception as e:
        from loguru import logger

        logger.warning(f"Structured JSON export failed: {e}")

    metadata = {
        "title": parsed.title,
        "authors": display_author_names,
        "abstract": parsed.abstract,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,  # Return the directory path
        "arxiv_id": arxiv_id,
        "structured_export": structured_export,
    }

    return result, metadata


def _populate_section_markdown(
    section,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> None:
    """Populate markdown for section and children."""
    if figure_counter is None:
        figure_counter = [0]
    if section.html:
        section.markdown = convert_fragment_to_markdown(
            section.html,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )
    for child in section.children:
        _populate_section_markdown(
            child,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )
