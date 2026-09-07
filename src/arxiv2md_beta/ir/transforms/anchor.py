"""Anchor pass: ensure stable anchors on all blocks and sections."""

from __future__ import annotations

import re

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms._anchors import slugify, unique_slug
from arxiv2md_beta.ir.transforms.base import IRPass

# arXiv section fragments ("S4", "S4.SS1", deeper "S4.SS1.SSS2"). The HTML
# builder leaves these raw in link target_ids because real section anchors
# are title slugs that only exist after this pass.
_SECTION_FRAGMENT_RE = re.compile(r"^S\d+(?:\.S{2,3}\d+)*$")


class AnchorPass(IRPass):
    """Generate stable anchors on sections and blocks.

    Uses ``struct_id`` for sections and ``figure_id``/``table_id``/etc.
    for blocks.  If no anchor exists, one is derived from the order.

    All anchors already present on the document (builder-set and those
    assigned by :class:`NumberingPass`) are pre-scanned into a used-set, and
    every anchor assigned here is uniquified against it — two sections with
    the same title get ``discussion`` / ``discussion-2`` instead of duplicate
    HTML ids.
    """

    name = "anchor"
    description = "Ensure every section and numbered block has an anchor."

    def run(self, doc: DocumentIR) -> DocumentIR:
        self._used: set[str] = set()
        self._eq_counter = 0
        self._prescan(doc)

        # Anchor abstract and front-matter blocks too, not just sections —
        # previously these were skipped while NumberingPass processed them,
        # so abstract figures/equations ended up with no anchor.
        for block in doc.abstract:
            self._anchor_block(block)
        for block in doc.front_matter:
            self._anchor_block(block)
        for section in doc.sections:
            self._anchor_section(section)

        self._repoint_section_fragments(doc)
        return doc

    # ── fragment repointing ────────────────────────────────────────────

    def _repoint_section_fragments(self, doc: DocumentIR) -> None:
        """Repoint raw arXiv section fragments (``S4``, ``S4.SS1``) to real anchors.

        The builder's old positional guess (``section-4-1``) never matched the
        slugified anchors actually emitted, so in-document section links were
        dead. Figure/table/algorithm fragments keep their build-time mapping
        (those ids coincide with NumberingPass ids).
        """
        fragment_map: dict[str, str] = {}

        def index_section(section: SectionIR, path: list[int]) -> None:
            key = ".".join(("S" if i == 0 else "SS" if i == 1 else "SSS") + str(n) for i, n in enumerate(path))
            if section.anchor:
                fragment_map[key] = section.anchor
            for j, child in enumerate(section.children, start=1):
                index_section(child, [*path, j])

        for i, section in enumerate(doc.sections, start=1):
            index_section(section, [i])

        if not fragment_map:
            return

        for block in doc.abstract:
            self._sweep_block_links([block], fragment_map)
        for block in doc.front_matter:
            self._sweep_block_links([block], fragment_map)
        for section in doc.sections:
            self._sweep_section_links(section, fragment_map)

    def _sweep_section_links(self, section: SectionIR, fragment_map: dict[str, str]) -> None:
        self._sweep_block_links(section.blocks, fragment_map)
        for child in section.children:
            self._sweep_section_links(child, fragment_map)

    def _sweep_block_links(self, blocks: list, fragment_map: dict[str, str]) -> None:
        for block in blocks:
            t = block.type
            if t in ("paragraph", "heading"):
                self._sweep_inline_links(getattr(block, "inlines", []), fragment_map)
            elif t in ("figure", "algorithm"):
                self._sweep_inline_links(getattr(block, "caption", []), fragment_map)
            elif t == "table":
                for cell in getattr(block, "headers", []):
                    self._sweep_inline_links(cell, fragment_map)
                for row in getattr(block, "rows", []):
                    for cell in row:
                        self._sweep_inline_links(cell, fragment_map)
                self._sweep_inline_links(getattr(block, "caption", []), fragment_map)
            elif t == "list":
                for item in block.items:
                    self._sweep_block_links(item, fragment_map)
            elif t == "blockquote":
                self._sweep_block_links(block.blocks, fragment_map)

    def _sweep_inline_links(self, inlines: list, fragment_map: dict[str, str]) -> None:
        for il in inlines:
            if (
                getattr(il, "type", None) == "link"
                and getattr(il, "kind", None) == "internal"
                and _SECTION_FRAGMENT_RE.match(il.target_id or "")
            ):
                mapped = fragment_map.get(il.target_id or "")
                if mapped:
                    il.target_id = mapped
            nested = getattr(il, "inlines", None)
            if nested:
                self._sweep_inline_links(nested, fragment_map)

    # ── prescan ────────────────────────────────────────────────────────

    def _prescan(self, doc: DocumentIR) -> None:
        def scan_block(block) -> None:
            anchor = getattr(block, "anchor", None)
            if anchor:
                self._used.add(anchor)
            t = block.type
            if t == "blockquote":
                for child in block.blocks:
                    scan_block(child)
            elif t == "list":
                for item in block.items:
                    for child in item:
                        scan_block(child)

        def scan_section(section: SectionIR) -> None:
            if section.anchor:
                self._used.add(section.anchor)
            for block in section.blocks:
                scan_block(block)
            for child in section.children:
                scan_section(child)

        for block in doc.abstract:
            scan_block(block)
        for block in doc.front_matter:
            scan_block(block)
        for section in doc.sections:
            scan_section(section)

    # ── assignment ─────────────────────────────────────────────────────

    def _anchor_section(self, section: SectionIR) -> None:
        if not section.anchor:
            base = section.struct_id or slugify(section.title)
            section.anchor = unique_slug(base, self._used)

        for block in section.blocks:
            self._anchor_block(block)

        for child in section.children:
            self._anchor_section(child)

    def _anchor_block(self, block) -> None:
        t = block.type
        if t == "figure":
            if not block.anchor:
                block.anchor = unique_slug(block.figure_id or block.label or "figure", self._used)
        elif t == "table":
            if not block.anchor:
                block.anchor = unique_slug(block.table_id or block.label or "table", self._used)
        elif t == "equation":
            if not block.anchor:
                # Equation numbers arrive parenthesized ("(3)"); anchors must
                # be bare digits ("eq-3") to stay valid HTML ids.
                num = str(block.equation_number or "").strip().strip("()[]")
                if not num:
                    # Unnumbered equation: fall back to a document-ordinal id
                    # ("eq-?" is not a valid HTML id).
                    self._eq_counter += 1
                    while f"eq-{self._eq_counter}" in self._used:
                        self._eq_counter += 1
                    num = str(self._eq_counter)
                block.anchor = unique_slug(block.label or f"eq-{num}", self._used)
        elif t == "algorithm":
            if not block.anchor:
                num = str(block.algorithm_number or "").strip()
                base = block.label or (f"alg-{num}" if num else "algorithm")
                block.anchor = unique_slug(base, self._used)
        elif t == "heading":
            if not block.anchor and block.label:
                block.anchor = unique_slug(block.label, self._used)
        elif t == "blockquote":
            for child in block.blocks:
                self._anchor_block(child)
        elif t == "list":
            for item in block.items:
                for child in item:
                    self._anchor_block(child)
