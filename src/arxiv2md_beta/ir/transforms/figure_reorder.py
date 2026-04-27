"""Figure reorder pass: move figures to their first citation paragraph.

Port of the existing ``reorder_figures_to_first_reference`` logic from
:mod:`arxiv2md_beta.output.formatter`, operating at the IR level.
"""

from __future__ import annotations

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass


class FigureReorderPass(IRPass):
    """Reorder figures so each appears after its first citation.

    Scans paragraphs for citations like ``Figure 1`` or ``Figure #``
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
        # Collect figures and their citations
        figures: dict[str, int] = {}  # figure_id → index in blocks
        for i, block in enumerate(blocks):
            if block.type == "figure" and block.figure_id:
                figures[block.figure_id] = i

        if not figures:
            return

        # Find first citation of each figure in paragraph text
        import re
        first_cite: dict[str, int] = {}  # figure_id → paragraph index
        for i, block in enumerate(blocks):
            if block.type != "paragraph":
                continue
            text = _inlines_to_text(getattr(block, "inlines", []))
            # Look for "Figure N" citations
            for m in re.finditer(r"Figure\s+(\d+)", text, re.I):
                fig_id = f"figure-{m.group(1)}"
                if fig_id in figures and fig_id not in first_cite:
                    first_cite[fig_id] = i

        # Move each figure to after its first citation
        for fig_id, fig_idx in sorted(figures.items(), reverse=True):
            cite_idx = first_cite.get(fig_id)
            if cite_idx is not None and cite_idx < fig_idx:
                # Figure is after its citation — move it
                figure = blocks.pop(fig_idx)
                # Insert after the citing paragraph
                insert_pos = cite_idx + 1
                blocks.insert(insert_pos, figure)


def _inlines_to_text(inlines: list) -> str:
    """Extract plain text from a list of inline nodes for pattern matching."""
    parts = []
    for il in inlines:
        if hasattr(il, "text"):
            parts.append(il.text)
        elif hasattr(il, "inlines"):
            parts.append(_inlines_to_text(il.inlines))
    return " ".join(parts)
