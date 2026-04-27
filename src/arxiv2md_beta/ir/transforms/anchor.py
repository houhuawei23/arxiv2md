"""Anchor pass: ensure stable anchors on all blocks and sections."""

from __future__ import annotations

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass


class AnchorPass(IRPass):
    """Generate stable anchors on sections and blocks.

    Uses ``struct_id`` for sections and ``figure_id``/``table_id``/etc.
    for blocks.  If no anchor exists, one is derived from the order.
    """

    name = "anchor"
    description = "Ensure every section and numbered block has an anchor."

    def run(self, doc: DocumentIR) -> DocumentIR:
        for section in doc.sections:
            self._anchor_section(section)
        return doc

    def _anchor_section(self, section: SectionIR) -> None:
        if not section.anchor:
            section.anchor = section.struct_id or _slugify(section.title)

        for block in section.blocks:
            self._anchor_block(block)

        for child in section.children:
            self._anchor_section(child)

    def _anchor_block(self, block) -> None:
        t = block.type
        if t == "figure":
            if not block.anchor:
                block.anchor = block.figure_id or block.label
        elif t == "table":
            if not block.anchor:
                block.anchor = block.table_id or block.label
        elif t == "equation":
            if not block.anchor:
                block.anchor = block.label or f"eq-{block.equation_number or '?'}"
        elif t == "algorithm":
            if not block.anchor:
                block.anchor = block.label or f"alg-{block.algorithm_number or '?'}"
        elif t == "heading":
            if not block.anchor and block.label:
                block.anchor = block.label
        elif t == "blockquote":
            for child in block.blocks:
                self._anchor_block(child)
        elif t == "list":
            for item in block.items:
                for child in item:
                    self._anchor_block(child)


def _slugify(title: str) -> str:
    """Convert a section title to a URL-friendly slug."""
    import re
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug[:60]
