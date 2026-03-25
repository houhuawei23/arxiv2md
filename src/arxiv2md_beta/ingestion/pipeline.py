"""Main ingestion pipeline orchestrator."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.ingestion.html import ingest_paper_html
from arxiv2md_beta.ingestion.latex import ingest_paper_latex
from arxiv2md_beta.schemas import IngestionResult


async def ingest_paper(
    *,
    arxiv_id: str,
    version: str | None,
    html_url: str,
    ar5iv_url: str | None = None,
    parser: str = "html",
    remove_refs: bool = False,
    remove_toc: bool = False,
    remove_inline_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    base_output_dir: Path,
    no_images: bool = False,
    source: str = "Arxiv",
    short: str | None = None,
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
    """Main ingestion function that routes to HTML or LaTeX parser.

    Parameters
    ----------
    arxiv_id : str
        arXiv ID
    version : str | None
        Version string
    html_url : str
        HTML URL (used for HTML parser)
    ar5iv_url : str | None
        ar5iv fallback URL
    parser : str
        Parser mode: "html" or "latex"
    remove_refs : bool
        Remove bibliography
    remove_toc : bool
        Remove table of contents
    remove_inline_citations : bool
        Remove inline citations
    section_filter_mode : str
        "include" or "exclude"
    sections : list[str] | None
        Section titles to filter
    base_output_dir : Path
        Base output directory (paper-specific directory will be created inside)
    no_images : bool
        Skip image processing

    Returns
    -------
    tuple[IngestionResult, dict]
        Result and metadata
    """
    sections = sections or []

    if parser == "latex":
        return await ingest_paper_latex(
            arxiv_id=arxiv_id,
            version=version,
            base_output_dir=base_output_dir,
            no_images=no_images,
            source=source,
            short=short,
        )
    else:  # html
        return await ingest_paper_html(
            arxiv_id=arxiv_id,
            version=version,
            html_url=html_url,
            ar5iv_url=ar5iv_url,
            remove_refs=remove_refs,
            remove_toc=remove_toc,
            remove_inline_citations=remove_inline_citations,
            section_filter_mode=section_filter_mode,
            sections=sections,
            base_output_dir=base_output_dir,
            no_images=no_images,
            source=source,
            short=short,
        )
