"""Shared DocumentIR build steps for the local-archive and local-HTML paths.

The four input paths build the same IR with the same pass pipeline; only the
builder and the image resolver inputs differ. These helpers replace the
copy-pasted ``_build_ir`` closures that previously drifted between the
remote and local implementations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.ir.resolvers import ImageResolver
from arxiv2md_beta.ir.transforms import build_default_pipeline
from arxiv2md_beta.settings import get_settings


def _default_pipeline(
    parser: Literal["html", "latex"],
    section_filter_mode: str,
    sections: list[str],
    remove_refs: bool,
):
    return build_default_pipeline(
        parser=parser,
        section_filter_mode=section_filter_mode,
        selected_sections=sections or [],
        remove_refs=remove_refs,
        reference_section_titles=get_settings().ingestion.reference_section_titles,
    )


def build_html_document(
    parsed: object,
    *,
    arxiv_id: str,
    image_stem_map: dict[str, Path],
    images_subdir: str,
    section_filter_mode: str,
    sections: list[str] | None,
    remove_refs: bool,
    paper_output_dir: Path,
) -> DocumentIR:
    """Parse already-extracted arXiv HTML into a transformed ``DocumentIR``."""
    from arxiv2md_beta.ingestion.ir_finalize import persist_inline_svgs
    from arxiv2md_beta.ir import HTMLBuilder

    doc = HTMLBuilder(
        image_resolver=ImageResolver(stem_map=image_stem_map),
        images_subdir=images_subdir,
    ).build(parsed, arxiv_id=arxiv_id)
    _default_pipeline("html", section_filter_mode, sections or [], remove_refs).run(doc)
    persist_inline_svgs(doc, paper_output_dir)
    return doc


def build_latex_document(
    tex_content: str,
    *,
    arxiv_id: str,
    image_path_map: dict[str, Path],
    base_dir: Path,
    title: str | None,
    authors: list[str] | None,
    abstract: str | None,
    images_subdir: str,
    section_filter_mode: str,
    sections: list[str] | None,
    remove_refs: bool,
) -> DocumentIR:
    """Parse resolved LaTeX source into a transformed ``DocumentIR``."""
    from arxiv2md_beta.ir import LaTeXBuilder

    doc = LaTeXBuilder(image_resolver=ImageResolver(path_map=image_path_map)).build(
        tex_content,
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        base_dir=base_dir,
        images_subdir=images_subdir,
    )
    _default_pipeline("latex", section_filter_mode, sections or [], remove_refs).run(doc)
    return doc
