"""Main ingestion pipeline orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    remove_inline_citations: bool = False,
    linked_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    base_output_dir: Path,
    no_images: bool = False,
    source: str = "Arxiv",
    short: str | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
    use_cache: bool = True,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Main ingestion function that routes to HTML or LaTeX parser.

    Parameters
    ----------
    arxiv_id : str
        arXiv paper ID
    version : str | None
        Version string
    html_url : str
        HTML URL (used for HTML parser)
    ar5iv_url : str | None
        ar5iv fallback URL
    parser : str
        Parser mode: only "latex" is supported here (remote HTML goes through
        :class:`~arxiv2md_beta.ingestion.orchestrator.IngestionOrchestrator`,
        routed by the CLI layer)
    remove_refs : bool
        Remove bibliography
    remove_inline_citations : bool
        Remove inline citations
    linked_citations : bool
        Render inline citations as [N](#ref-N) links
    section_filter_mode : str
        "include" or "exclude"
    sections : list[str] | None
        Section titles to filter
    base_output_dir : Path
        Base output directory (paper-specific directory will be created inside)
    no_images : bool
        Skip image processing
    use_cache : bool
        Use download caching for TeX source, HTML, and PDF (default: True)

    Returns:
    -------
    tuple[IngestionResult, dict]
        Result and metadata
    """
    sections = sections or []

    if parser != "latex":
        raise ValueError(
            "ingest_paper only supports the LaTeX parser; the remote HTML path "
            "is IngestionOrchestrator (routed by cli.runner.convert)."
        )
    return await ingest_paper_latex(
        arxiv_id=arxiv_id,
        version=version,
        base_output_dir=base_output_dir,
        remove_refs=remove_refs,
        remove_inline_citations=remove_inline_citations,
        linked_citations=linked_citations,
        section_filter_mode=section_filter_mode,
        sections=sections,
        no_images=no_images,
        source=source,
        short=short,
        structured_output=structured_output,
        emit_graph_csv=emit_graph_csv,
        use_cache=use_cache,
    )
