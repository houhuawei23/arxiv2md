"""Figure reorder pass: move figures to their first citation paragraph.

Port of the existing ``reorder_figures_to_first_reference`` logic from
:mod:`arxiv2md_beta.output.formatter`, operating at the IR level.
"""

from __future__ import annotations

import re

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.inlines import (
    EmphasisIR,
    ImageRefIR,
    LinkIR,
    MathIR,
    RawInlineIR,
    SubscriptIR,
    SuperscriptIR,
    TextIR,
)
from arxiv2md_beta.ir.transforms.base import IRPass

# Match "Figure 3", "Fig. 3", "Fig 3", "figure3" — case-insensitive.
_FIGURE_CITATION_RE = re.compile(r"Fig(?:ure)?\.?\s*(\d+)", re.I)


class FigureReorderPass(IRPass):
    """Reorder figures so each appears after its first citation.

    Scans paragraphs for citations like ``Figure 1`` or ``Fig. 1``
    and moves the referenced figure block right after the first
    paragraph that cites it.
    """

    name = "figure_reorder"
    description = "Move figures to sit after their first citation paragraph."

    def run(self, doc: DocumentIR) -> DocumentIR:
        # Process abstract then sections
        self._reorder_in_blocks(doc.abstract)
        for section in doc.sections:
            self._reorder_section(section)
        return doc

    def _reorder_section(self, section: SectionIR) -> None:
        self._reorder_in_blocks(section.blocks)
        for child in section.children:
            self._reorder_section(child)

    def _reorder_in_blocks(self, blocks: list) -> None:
        # Collect figures by identity; indices go stale as blocks are moved,
        # so every insertion re-locates the figure and its citing paragraph.
        figures: dict[str, object] = {}  # figure_id → figure block
        for block in blocks:
            if block.type == "figure" and block.figure_id:
                figures[block.figure_id] = block

        if not figures:
            return

        # Find first citation of each figure in paragraph text
        first_cite: dict[str, object] = {}  # figure_id → citing paragraph block
        for block in blocks:
            if block.type != "paragraph":
                continue
            text = _inlines_to_text(getattr(block, "inlines", []))
            # Look for "Figure N" / "Fig. N" citations
            for m in _FIGURE_CITATION_RE.finditer(text):
                fig_id = f"figure-{m.group(1)}"
                if fig_id in figures and fig_id not in first_cite:
                    first_cite[fig_id] = block

        # Move each figure to after its first citation
        for fig_id, figure in figures.items():
            para = first_cite.get(fig_id)
            if para is None:
                continue
            fig_idx = _index_of(blocks, figure)
            para_idx = _index_of(blocks, para)
            if fig_idx is None or para_idx is None or para_idx >= fig_idx:
                continue
            blocks.pop(fig_idx)
            blocks.insert(para_idx + 1, figure)


def _index_of(blocks: list, obj: object) -> int | None:
    """Index of *obj* in *blocks* by identity (``list.index`` uses ``==``)."""
    for i, block in enumerate(blocks):
        if block is obj:
            return i
    return None


def _inlines_to_text(inlines: list) -> str:
    """Extract plain text from a list of inline nodes for pattern matching.

    Covers every concrete :data:`InlineUnion` member so citations embedded in
    math, image alt-text, link labels, or raw inline content are visible to
    figure-citation matching. Previously used ``hasattr`` and only saw
    ``TextIR.text`` + nested ``inlines``, missing math/alt/url/raw content.
    """
    parts: list[str] = []
    for il in inlines:
        if isinstance(il, TextIR):
            parts.append(il.text)
        elif isinstance(il, MathIR):
            parts.append(il.latex)
        elif isinstance(il, ImageRefIR):
            parts.append(il.alt)
        elif isinstance(il, RawInlineIR):
            parts.append(il.content)
        elif isinstance(il, LinkIR):
            if il.url:
                parts.append(il.url)
            parts.append(_inlines_to_text(il.inlines))
        elif isinstance(il, EmphasisIR | SuperscriptIR | SubscriptIR):
            parts.append(_inlines_to_text(il.inlines))
        # BreakIR has no text content; skip.
    return " ".join(parts)
