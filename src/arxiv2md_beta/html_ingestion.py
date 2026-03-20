"""HTML ingestion pipeline for arXiv HTML -> Markdown with image support."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.arxiv_api import fetch_arxiv_metadata
from arxiv2md_beta.fetch import fetch_arxiv_html
from arxiv2md_beta.html_parser import parse_arxiv_html
from arxiv2md_beta.image_resolver import process_images
from arxiv2md_beta.markdown import convert_fragment_to_markdown
from arxiv2md_beta.output_formatter import format_paper
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.sections import filter_sections
from arxiv2md_beta.tex_source import TexSourceNotFoundError, fetch_and_extract_tex_source

_REFERENCE_TITLES = ("references", "bibliography")
_ABSTRACT_TITLE = "abstract"


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

    # Fetch metadata from API to get submission date (more reliable)
    api_metadata = await fetch_arxiv_metadata(arxiv_id)
    # Use API date if available, otherwise use parsed date
    submission_date = api_metadata.get("submission_date") or parsed.submission_date
    # Use API title if parsed title is None
    if not parsed.title and api_metadata.get("title"):
        parsed.title = api_metadata["title"]

    filtered_sections = filter_sections(parsed.sections, mode=section_filter_mode, selected=sections)
    if remove_refs:
        filtered_sections = filter_sections(filtered_sections, mode="exclude", selected=_REFERENCE_TITLES)

    # Check if abstract should be included based on section filter
    selected_lower = [s.lower() for s in sections]
    if section_filter_mode == "exclude":
        include_abstract = _ABSTRACT_TITLE not in selected_lower
    else:  # include mode
        include_abstract = not sections or _ABSTRACT_TITLE in selected_lower

    # Create paper-specific output directory
    from arxiv2md_beta.cli import create_paper_output_dir
    paper_output_dir = create_paper_output_dir(
        base_output_dir, submission_date, parsed.title, source=source, short=short
    )
    images_dir_name = "images"
    images_dir = paper_output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)
    
    # Process images if enabled
    image_map: dict[int, Path] | None = None
    if not no_images:
        try:
            tex_source_info = await fetch_and_extract_tex_source(arxiv_id, version=version)
            processed_images = process_images(tex_source_info, paper_output_dir, images_dir_name)
            image_map = processed_images.image_map
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
            figure_counter=figure_counter,
            images_dir=images_dir,
        )

    result = format_paper(
        arxiv_id=arxiv_id,
        version=version,
        title=parsed.title,
        authors=parsed.authors,
        abstract=abstract_md if include_abstract else None,
        sections=filtered_sections,
        include_toc=not remove_toc,
        include_abstract_in_tree=parsed.abstract is not None,
    )

    # Save paper metadata to paper.yml
    try:
        from arxiv2md_beta.paper_metadata import save_paper_metadata
        save_paper_metadata(api_metadata, paper_output_dir)
    except Exception as e:
        from loguru import logger
        logger.warning(f"Failed to save paper.yml: {e}")

    metadata = {
        "title": parsed.title,
        "authors": parsed.authors,
        "abstract": parsed.abstract,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,  # Return the directory path
    }

    return result, metadata


def _populate_section_markdown(
    section,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
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
            figure_counter=figure_counter,
            images_dir=images_dir,
        )
    for child in section.children:
        _populate_section_markdown(
            child,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )
