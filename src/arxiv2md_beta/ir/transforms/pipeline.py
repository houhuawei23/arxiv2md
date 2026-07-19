"""Canonical IR transform pipeline factory.

Every ingestion path (remote HTML, remote LaTeX, local HTML, local archive)
must run the same ordered pass sequence; before this factory existed each of
the five call sites hand-built its own ``PassPipeline`` and they drifted:

* remote LaTeX added ``SectionNumberingPass``; local LaTeX archive did not
  -> numbering diverged between the two LaTeX paths.
* local archives did not add ``SectionFilterPass`` at all, so ``--sections``
  / ``--remove-refs`` were silently ignored for local inputs.
* ``local_html`` added ``SectionFilterPass`` unconditionally (even with an
  empty selection), the others only conditionally.

``build_default_pipeline`` is now the single source of truth. Pass order is
order-sensitive: ``FigureReorderPass`` consumes ``figure_id`` set by
``NumberingPass``; ``AnchorPass`` must run last on the final structure.
``SectionNumberingPass`` prefixes LaTeX section titles (``1.2.3``) and is a
no-op for HTML, so it is included only for the LaTeX parser.
"""

from __future__ import annotations

from typing import Literal

from arxiv2md_beta.ir.transforms.anchor import AnchorPass
from arxiv2md_beta.ir.transforms.base import PassPipeline
from arxiv2md_beta.ir.transforms.figure_reorder import FigureReorderPass
from arxiv2md_beta.ir.transforms.numbering import NumberingPass, SectionNumberingPass
from arxiv2md_beta.ir.transforms.section_filter import SectionFilterPass

ParserKind = Literal["html", "latex"]


def build_default_pipeline(
    *,
    parser: ParserKind,
    section_filter_mode: str = "exclude",
    selected_sections: list[str] | None = None,
    remove_refs: bool = False,
    reference_section_titles: list[str] | None = None,
) -> PassPipeline:
    """Build the canonical pass pipeline for an ingestion path.

    Parameters
    ----------
    parser:
        ``"latex"`` includes :class:`SectionNumberingPass` (no-op for HTML).
    section_filter_mode / selected_sections:
        When *selected_sections* is non-empty, a :class:`SectionFilterPass`
        runs first (before numbering, so filtered sections are not numbered).
    remove_refs:
        Adds an exclude filter against *reference_section_titles*.
    """
    selected = list(selected_sections or [])
    pipeline = PassPipeline()
    if selected:
        pipeline.add(SectionFilterPass(mode=section_filter_mode, selected=selected))
    if remove_refs:
        pipeline.add(
            SectionFilterPass(
                mode="exclude",
                selected=list(reference_section_titles or []),
            )
        )
    pipeline.add(NumberingPass())
    if parser == "latex":
        pipeline.add(SectionNumberingPass())
    pipeline.add(FigureReorderPass())
    pipeline.add(AnchorPass())
    return pipeline
