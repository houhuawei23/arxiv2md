"""LaTeX ingestion pipeline for arXiv LaTeX -> Markdown with image support."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.network.arxiv_api import author_display_names_from_metadata, fetch_arxiv_metadata
from arxiv2md_beta.images.resolver import process_images
from arxiv2md_beta.latex.parser import ParserNotAvailableError, parse_latex_to_markdown
from arxiv2md_beta.output.formatter import format_paper
from arxiv2md_beta.schemas import IngestionResult, SectionNode
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.latex.tex_source import TexSourceNotFoundError, fetch_and_extract_tex_source

from loguru import logger


async def ingest_paper_latex(
    *,
    arxiv_id: str,
    version: str | None,
    base_output_dir: Path,
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
    no_images : bool
        If True, skip image downloading and processing

    Returns
    -------
    tuple[IngestionResult, dict]
        Ingestion result and metadata

    Raises
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
        processed_images = process_images(tex_source_info, paper_output_dir, images_dir_name)

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

    # Create a simple section structure from the markdown
    # For LaTeX mode, we create a single section with the full content
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

    display_author_names = author_display_names_from_metadata(api_metadata) or list(parsed_latex.authors or [])

    # Format output (single blob; no HTML section tree — do not split into References/Appendix files)
    result = format_paper(
        arxiv_id=arxiv_id,
        version=version,
        title=parsed_latex.title,
        authors=display_author_names,
        abstract=parsed_latex.abstract,
        sections=sections,
        include_toc=False,  # LaTeX mode doesn't use TOC
        include_abstract_in_tree=parsed_latex.abstract is not None,
        split_for_reference=False,
    )

    # Save paper metadata to paper.yml
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata
        save_paper_metadata(api_metadata, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    structured_export: dict[str, object] = {}
    try:
        from arxiv2md_beta.output.structured_export import (
            normalize_structured_mode,
            write_minimal_structured,
        )

        sm = normalize_structured_mode(structured_output)
        if sm != "none":
            stem_map = processed_images.stem_to_image_path if processed_images else None
            img_map = processed_images.image_map if processed_images else None
            structured_export = write_minimal_structured(
                paper_output_dir=paper_output_dir,
                mode=sm,
                emit_graph_csv=emit_graph_csv,
                arxiv_id=arxiv_id,
                arxiv_version=version,
                title=parsed_latex.title or title,
                authors=list(parsed_latex.authors or []),
                submission_date=submission_date,
                parser="latex",
                sections=sections,
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
