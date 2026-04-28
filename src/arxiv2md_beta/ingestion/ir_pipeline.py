"""IR-based ingestion pipeline: Builder → Transforms → Emitter.

This module provides an alternative ingestion path that uses the
three-tier IR architecture instead of the legacy markdown-based pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arxiv2md_beta.ir import (
    AnchorPass,
    FigureReorderPass,
    HTMLBuilder,
    LaTeXBuilder,
    MarkdownEmitter,
    NumberingPass,
    PassPipeline,
    SectionFilterPass,
)
from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.schemas import IngestionResult, SectionNode


def build_document_from_html(
    html: str,
    arxiv_id: str,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
) -> DocumentIR:
    """Build a :class:`DocumentIR` from arXiv HTML.

    Parameters
    ----------
    html : str
        Raw HTML content.
    arxiv_id : str
        arXiv identifier.
    image_map : dict[int, Path] | None
        Figure index → local image path.
    image_stem_map : dict[str, Path] | None
        Image stem → local image path.
    """
    builder = HTMLBuilder(image_map=image_map, image_stem_map=image_stem_map)
    return builder.build(html, arxiv_id=arxiv_id)


def build_document_from_latex(
    tex_content: str,
    arxiv_id: str,
    image_map: dict[str, Path] | None = None,
    title: str | None = None,
    authors: list[str] | None = None,
    abstract: str | None = None,
) -> DocumentIR:
    """Build a :class:`DocumentIR` from LaTeX content via Pandoc JSON AST.

    Parameters
    ----------
    tex_content : str
        Expanded LaTeX content (after ``\\input``/``\\include`` resolution).
    arxiv_id : str
        arXiv identifier.
    image_map : dict[str, Path] | None
        LaTeX image path/label → local image path.
    title : str | None
        Pre-extracted title.
    authors : list[str] | None
        Pre-extracted author names.
    abstract : str | None
        Pre-extracted abstract text.
    """
    builder = LaTeXBuilder(image_map=image_map)
    return builder.build(
        tex_content,
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
    )


def create_default_pipeline(
    *,
    reorder_figures: bool = True,
    remove_inline_citations: bool = False,
    section_filter: tuple[str, list[str]] | None = None,
) -> PassPipeline:
    """Create the default transform pipeline.

    Parameters
    ----------
    reorder_figures : bool
        Move figures to first citation paragraph.
    remove_inline_citations : bool
        Remove inline citation markers.
    section_filter : tuple[str, list[str]] | None
        (mode, titles) for section filtering.
    """
    pp = PassPipeline()
    pp.add(NumberingPass())
    if reorder_figures:
        pp.add(FigureReorderPass())
    if section_filter:
        mode, titles = section_filter
        pp.add(SectionFilterPass(mode=mode, selected=titles))
    pp.add(AnchorPass())
    return pp


def document_to_ingestion_result(
    doc: DocumentIR,
    include_toc: bool = True,
) -> IngestionResult:
    """Convert a :class:`DocumentIR` to an :class:`IngestionResult`.

    Parameters
    ----------
    doc : DocumentIR
        The IR document.
    include_toc : bool
        Whether to generate a table of contents section in the output.
    """
    emitter = MarkdownEmitter()
    content = emitter.emit(doc)

    # Build summary
    m = doc.metadata
    author_names = m.author_names
    summary_parts = [
        f"# {m.title or 'Untitled'}",
        "",
        f"**Authors:** {', '.join(author_names) if author_names else 'Unknown'}",
    ]
    if m.submission_date:
        summary_parts.append(f"**Date:** {m.submission_date}")
    if m.arxiv_id:
        summary_parts.append(f"**arXiv ID:** {m.arxiv_id}")
    if m.abstract_text:
        summary_parts.append(f"\n## Abstract\n\n{m.abstract_text}")

    # Build sections tree
    tree_lines = ["Sections:"]
    _build_section_tree_lines(doc.sections, tree_lines, indent=0)
    sections_tree = "\n".join(tree_lines)

    return IngestionResult(
        summary="\n".join(summary_parts),
        sections_tree=sections_tree,
        content=content,
    )


def _build_section_tree_lines(
    sections: list[Any], lines: list[str], indent: int = 0
) -> None:
    """Recursively build section tree lines."""
    prefix = "  " * indent
    for sec in sections:
        title = getattr(sec, "title", "") or ""
        lines.append(f"{prefix}- {title}")
        children = getattr(sec, "children", [])
        if children:
            _build_section_tree_lines(children, lines, indent + 1)


def sections_to_ingestion_result(
    content: str,
    summary: str,
    sections: list[SectionNode],
) -> IngestionResult:
    """Build an :class:`IngestionResult` with a sections tree.

    Used when the caller already has section metadata from the legacy parser.
    """
    tree_lines = ["Sections:"]
    _build_section_tree_lines_from_nodes(sections, tree_lines, indent=0)
    sections_tree = "\n".join(tree_lines)

    return IngestionResult(
        summary=summary,
        sections_tree=sections_tree,
        content=content,
    )


def _build_section_tree_lines_from_nodes(
    sections: list[SectionNode], lines: list[str], indent: int = 0
) -> None:
    prefix = "  " * indent
    for sec in sections:
        lines.append(f"{prefix}- {sec.title}")
        if sec.children:
            _build_section_tree_lines_from_nodes(sec.children, lines, indent + 1)
