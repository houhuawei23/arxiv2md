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

# Match "Figure 3", "Figures 3", "Fig. 3", "Fig 3", "figure3" —
# case-insensitive — plus conventional continuation lists ("… and 4",
# "…, 5", "…-7"): "Figures 3 and 4" is the standard multi-reference and the
# plural form used to match nothing at all (audit4 S4.1).
_FIGURE_CITATION_RE = re.compile(r"Fig(?:ures?|s)?\.?\s*(\d+)((?:\s*(?:,|and|&|to|-|–)\s*\d+)*)", re.I)
_CONTINUATION_NUM_RE = re.compile(r"\d+")


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
        # Multiple figures can share a caption id (appendix "Figure 1" after
        # body Figure 1); a fig_id-keyed dict let the later one overwrite the
        # earlier, which then never moved (audit5 I-7) — keep a queue per id.
        figures: dict[str, list] = {}  # figure_id → figure blocks, document order
        for block in blocks:
            if block.type == "figure" and block.figure_id:
                figures.setdefault(block.figure_id, []).append(block)

        if not figures:
            return

        # Find first citation of each figure in paragraph text; each citation
        # claims the first not-yet-claimed figure with that id.
        pending = {fid: list(fs) for fid, fs in figures.items()}
        moves: list[tuple[object, object]] = []  # (figure, citing paragraph)
        for block in blocks:
            if block.type != "paragraph":
                continue
            text = _inlines_to_text(getattr(block, "inlines", []))
            # Look for "Figure N" / "Fig. N" citations, including continuations
            for m in _FIGURE_CITATION_RE.finditer(text):
                nums = [m.group(1)] + _CONTINUATION_NUM_RE.findall(m.group(2))
                for n in nums:
                    queue = pending.get(f"figure-{n}")
                    if queue:
                        moves.append((queue.pop(0), block))
            # LaTeX path: a \ref{label} arrives as an internal link whose
            # target_id equals the figure's label (builder convention, audit4
            # B4). Without this, figure reordering was a structural no-op on
            # the LaTeX pipeline.
            for target in _link_target_ids(getattr(block, "inlines", [])):
                for queue in pending.values():
                    if queue and getattr(queue[0], "label", None) == target:
                        moves.append((queue.pop(0), block))
                        break

        # Move each figure to after its first citation. Positions live in an
        # identity-keyed dict, updated incrementally after each pop/insert —
        # replaces the O(F×B) linear identity rescan per move.
        if not moves:
            return

        pos: dict[int, int] = {id(b): i for i, b in enumerate(blocks)}
        # Figures sharing a citing paragraph stack in document order instead
        # of all landing on the same slot (which reversed their order).
        insert_offset: dict[int, int] = {}
        for figure, para in moves:
            fig_idx = pos.get(id(figure))
            para_idx = pos.get(id(para))
            if fig_idx is None or para_idx is None or para_idx >= fig_idx:
                continue
            blocks.pop(fig_idx)
            for bid, i in pos.items():
                if i > fig_idx:
                    pos[bid] = i - 1
            insert_at = para_idx + 1 + insert_offset.get(id(para), 0)
            insert_offset[id(para)] = insert_offset.get(id(para), 0) + 1
            blocks.insert(insert_at, figure)
            for bid, i in pos.items():
                if bid != id(figure) and i >= insert_at:
                    pos[bid] = i + 1
            pos[id(figure)] = insert_at


def _link_target_ids(inlines: list) -> list[str]:
    """Collect internal-link target_ids recursively from *inlines*."""
    out: list[str] = []
    for il in inlines:
        if isinstance(il, LinkIR):
            if il.target_id:
                out.append(il.target_id)
            out.extend(_link_target_ids(il.inlines))
        elif isinstance(il, EmphasisIR | SuperscriptIR | SubscriptIR):
            out.extend(_link_target_ids(il.inlines))
    return out


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
