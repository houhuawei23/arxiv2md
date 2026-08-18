"""Numbering pass: assign sequential numbers to figures, tables, equations, algorithms, and sections."""

from __future__ import annotations

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass


class SectionNumberingPass(IRPass):
    r"""Prepend hierarchical section numbers to section titles.

    Only runs when the document parser is ``"latex"`` (HTML from ar5iv already
    has numbers baked into the rendered heading text).  Starred / unnumbered
    sections (``SectionIR.unnumbered``) and paragraph-level run-in headings
    (``level >= 5``, i.e. ``\\paragraph`` and below) are skipped — they are not
    numbered in conventional LaTeX / ar5iv output.

    The numbering scheme is arabic dot-separated (``1``, ``1.1``, ``2.3.1``,
    …).  The result is written into the *title* so the MarkdownEmitter sees
    ``## 1 Introduction`` instead of ``## Introduction``.
    """

    name = "section-numbering"
    description = "Prepend hierarchical section numbers to LaTeX section titles."

    def run(self, doc: DocumentIR) -> DocumentIR:
        if doc.metadata.parser != "latex":
            return doc
        self._number_sections(doc.sections, counter=None)
        return doc

    @staticmethod
    def _number_sections(sections: list[SectionIR], counter: list[int] | None) -> None:
        """Walk *sections* depth-first, numbering each in place.

        *counter* is a mutable list of ints representing the current path
        (e.g. ``[1]`` for §1, ``[1, 2]`` for §1.2, ``[3, 4, 1]`` for
        §3.4.1).  ``None`` means we haven't started yet.
        """
        if counter is None:
            counter = []

        counter.append(0)
        for sec in sections:
            if sec.unnumbered or sec.level >= 5:
                # Children of unnumbered sections continue the sibling
                # sequence (LaTeX convention); restarting from 1 would
                # duplicate struct_ids like "sec_1" and break anchors.
                SectionNumberingPass._number_sections(sec.children, list(counter))
                continue

            counter[-1] += 1
            number_str = ".".join(str(n) for n in counter)
            sec.struct_id = f"sec_{number_str.replace('.', '_')}"
            sec.title = f"{number_str} {sec.title}"

            # Descend into children.
            SectionNumberingPass._number_sections(sec.children, counter)
        counter.pop()


class NumberingPass(IRPass):
    """Assign sequential IDs to numbered elements — the single numbering source.

    Walks the document and assigns ``figure_id``, ``table_id``,
    ``equation_number``, and ``algorithm_number`` fields. Builders only set
    ids extracted from captions (e.g. an appendix figure whose caption says
    "Figure 1"); this pass assigns ids to the rest and guarantees two
    invariants the builder/pass split used to break:

    - every assigned auto id does not collide with a caption-derived id
      (the counter skips already-claimed numbers);
    - every anchor is unique: when two elements share a caption-derived id
      (appendix "Figure 1" after body Figure 1), the second and later
      occurrences get ``{id}-2``, ``{id}-3``, … anchors. ``figure_id`` keeps
      the caption semantics.
    """

    name = "numbering"
    description = "Assign sequential numbers to figures, tables, equations, and algorithms."

    def run(self, doc: DocumentIR) -> DocumentIR:
        ctx = {"figure": 0, "table": 0, "equation": 0, "algorithm": 0}
        self._claimed: set[str] = set()
        self._used_anchors: set[str] = set()
        # arXiv fragment ids (e.g. "S1.F1") → final anchor. Internal links
        # carrying a raw fragment are re-pointed to the real anchor after
        # numbering, replacing the builder's global-counter guess.
        self._label_to_anchor: dict[str, str] = {}

        # Pre-scan: claim every caption-derived id up front so auto numbers
        # never collide with a caption id that appears later in the document.
        self._collect_claimed(doc)

        for block in doc.abstract:
            self._number_blocks([block], ctx)
        for section in doc.sections:
            self._number_section(section, ctx)

        self._repoint_fragment_links(doc)
        return doc

    def _collect_claimed(self, doc: DocumentIR) -> None:
        def walk(blocks: list) -> None:
            for block in blocks:
                t = block.type
                if t == "figure" and block.figure_id:
                    self._claimed.add(block.figure_id)
                elif t == "table" and block.table_id:
                    self._claimed.add(block.table_id)
                elif t == "equation":
                    num = (block.equation_number or "").strip().strip("()[]")
                    if num:
                        self._claimed.add(f"eq-{num}")
                elif t == "algorithm" and block.algorithm_number:
                    self._claimed.add(f"algorithm-{str(block.algorithm_number).strip()}")
                elif t == "list":
                    for item in block.items:
                        walk(item)
                elif t == "blockquote":
                    walk(block.blocks)

        for block in doc.abstract:
            walk([block])
        for section in doc.sections:
            self._walk_sections(section, walk)

    def _walk_sections(self, section: SectionIR, walk) -> None:
        walk(section.blocks)
        for child in section.children:
            self._walk_sections(child, walk)

    # ── id / anchor helpers ────────────────────────────────────────────

    def _next_id(self, kind: str, ctx: dict) -> str:
        """Next ``{kind}-{n}`` that no caption-derived id has claimed."""
        prefix = {"figure": "figure", "table": "table", "equation": "eq", "algorithm": "algorithm"}[kind]
        n = ctx[kind] + 1
        while f"{prefix}-{n}" in self._claimed:
            n += 1
        ctx[kind] = n
        return f"{prefix}-{n}"

    def _unique_anchor(self, base: str) -> str:
        """First of ``base``, ``base-2``, ``base-3``, … not yet used."""
        if base not in self._used_anchors:
            self._used_anchors.add(base)
            return base
        n = 2
        while f"{base}-{n}" in self._used_anchors:
            n += 1
        anchor = f"{base}-{n}"
        self._used_anchors.add(anchor)
        return anchor

    def _claim_and_anchor(self, block, block_id: str) -> None:
        """Register *block_id* as claimed and give *block* a unique anchor.

        The anchor is only rewritten when it mirrors the id (or was empty);
        label-based anchors set by builders are preserved as-is.
        """
        self._claimed.add(block_id)
        current = getattr(block, "anchor", None)
        if not current or current == block_id:
            block.anchor = self._unique_anchor(block_id)
        label = getattr(block, "label", None)
        if label:
            self._label_to_anchor[label] = block.anchor

    # ── fragment-link repointing ───────────────────────────────────────

    def _repoint_fragment_links(self, doc: DocumentIR) -> None:
        """Rewrite internal links whose target is a raw arXiv fragment id."""
        for block in doc.abstract:
            self._sweep_blocks([block])
        for section in doc.sections:
            self._sweep_section(section)

    def _sweep_section(self, section: SectionIR) -> None:
        self._sweep_blocks(section.blocks)
        for child in section.children:
            self._sweep_section(child)

    def _sweep_blocks(self, blocks: list) -> None:
        for block in blocks:
            t = block.type
            if t == "paragraph" or t == "heading":
                self._sweep_inlines(getattr(block, "inlines", []))
            elif t in ("figure", "algorithm"):
                self._sweep_inlines(getattr(block, "caption", []))
            elif t == "table":
                for cell in getattr(block, "headers", []):
                    self._sweep_inlines(cell)
                for row in getattr(block, "rows", []):
                    for cell in row:
                        self._sweep_inlines(cell)
                self._sweep_inlines(getattr(block, "caption", []))
            elif t == "list":
                for item in block.items:
                    self._sweep_blocks(item)
            elif t == "blockquote":
                self._sweep_blocks(block.blocks)

    def _sweep_inlines(self, inlines: list) -> None:
        for il in inlines:
            if getattr(il, "type", None) == "link" and getattr(il, "kind", None) == "internal":
                mapped = self._label_to_anchor.get(il.target_id or "")
                if mapped:
                    il.target_id = mapped
            nested = getattr(il, "inlines", None)
            if nested:
                self._sweep_inlines(nested)

    def _number_section(self, section: SectionIR, ctx: dict) -> None:
        self._number_blocks(section.blocks, ctx)
        for child in section.children:
            self._number_section(child, ctx)

    def _number_blocks(self, blocks: list, ctx: dict) -> None:
        for block in blocks:
            t = block.type
            if t == "figure":
                fid = block.figure_id or self._next_id("figure", ctx)
                block.figure_id = fid
                self._claim_and_anchor(block, fid)
            elif t == "table":
                tid = block.table_id or self._next_id("table", ctx)
                block.table_id = tid
                self._claim_and_anchor(block, tid)
            elif t == "equation":
                # equation_number arrives parenthesized ("(3)"); ids/anchors
                # use bare digits ("eq-3").
                num = (block.equation_number or "").strip().strip("()[]")
                eid = f"eq-{num}" if num else self._next_id("equation", ctx)
                if not block.equation_number:
                    block.equation_number = f"({eid.removeprefix('eq-')})"
                self._claim_and_anchor(block, eid)
            elif t == "algorithm":
                num = (block.algorithm_number or "").strip()
                aid = f"algorithm-{num}" if num else self._next_id("algorithm", ctx)
                if not block.algorithm_number:
                    block.algorithm_number = aid.removeprefix("algorithm-")
                self._claim_and_anchor(block, aid)
            elif t == "list":
                for item in block.items:
                    self._number_blocks(item, ctx)
            elif t == "blockquote":
                self._number_blocks(block.blocks, ctx)
