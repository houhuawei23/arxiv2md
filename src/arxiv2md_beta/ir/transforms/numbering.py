"""Numbering pass: assign sequential numbers to figures, tables, equations, algorithms, and sections."""

from __future__ import annotations

import re

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms._anchors import slugify, unique_slug
from arxiv2md_beta.ir.transforms.base import IRPass

# arXiv section fragments ("S4", "S4.SS1", deeper "S4.SS1.SSS2"). The HTML
# builder leaves these raw in link target_ids because real section anchors
# are title slugs that only exist after this pass.
_SECTION_FRAGMENT_RE = re.compile(r"^S\d+(?:\.S{2,3}\d+)*$")


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
        self._seen_struct_ids: set[str] = set()
        self._number_sections(doc.sections, counter=None)
        return doc

    def _unique_struct_id(self, base: str) -> str:
        """*base*, suffixed when a previous section already claimed it.

        Unnumbered sections' children re-enter numbering with their parent's
        sibling path, so two different sections can derive the same id
        (e.g. an unnumbered appendix overview's child vs. §1's child);
        duplicates would collide in the JSON graph and in anchors.
        """
        sid, n = base, 2
        while sid in self._seen_struct_ids:
            sid = f"{base}-{n}"
            n += 1
        self._seen_struct_ids.add(sid)
        return sid

    def _number_sections(self, sections: list[SectionIR], counter: list[int] | None) -> None:
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
                self._number_sections(sec.children, list(counter))
                continue

            counter[-1] += 1
            number_str = ".".join(str(n) for n in counter)
            sec.struct_id = self._unique_struct_id(f"sec_{number_str.replace('.', '_')}")
            # Strip exactly the prefix a previous run of this pass wrote (its
            # number_prefix field) — a regex guess would eat real titles that
            # start with a number ("2000 Swarms: A Survey" lost its first
            # word, audit5 G1-1).
            title = sec.title
            prefix = sec.number_prefix
            if prefix and title.startswith(prefix + " "):
                title = title[len(prefix) + 1 :]
            elif prefix and title == prefix:
                title = ""
            sec.title = f"{number_str} {title}" if title else number_str
            sec.number_prefix = number_str

            # Descend into children.
            self._number_sections(sec.children, counter)
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
    description = (
        "Assign sequential numbers and unique anchors to figures, tables, "
        "equations, algorithms, and sections (absorbs the former AnchorPass)."
    )

    def run(self, doc: DocumentIR) -> DocumentIR:
        ctx = {"figure": 0, "table": 0, "equation": 0, "algorithm": 0}
        self._claimed: set[str] = set()
        self._used_anchors: set[str] = set()
        self._eq_counter = 0
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

        # Register every anchor now present on the document so the slugs
        # assigned below cannot collide with an existing one.
        self._prescan_anchors(doc)

        # Section anchors + remaining block anchors (absorbed AnchorPass).
        self._anchor_front_matter(doc)
        self._anchor_sections(doc)

        self._repoint_fragment_links(doc)
        self._repoint_section_fragments(doc)
        return doc

    # ── anchor assignment (absorbed AnchorPass) ────────────────────────

    def _prescan_anchors(self, doc: DocumentIR) -> None:
        def scan_block(block) -> None:
            anchor = getattr(block, "anchor", None)
            if anchor:
                self._used_anchors.add(anchor)
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
                self._used_anchors.add(section.anchor)
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

    def _anchor_front_matter(self, doc: DocumentIR) -> None:
        for block in doc.front_matter:
            self._anchor_block(block)

    def _anchor_sections(self, doc: DocumentIR) -> None:
        for section in doc.sections:
            self._anchor_section(section)

    def _anchor_section(self, section: SectionIR) -> None:
        if not section.anchor:
            base = section.struct_id or slugify(section.title)
            section.anchor = unique_slug(base, self._used_anchors)

        for block in section.blocks:
            self._anchor_block(block)

        for child in section.children:
            self._anchor_section(child)

    def _anchor_block(self, block) -> None:
        """Give any still-unanchored numbered block a unique anchor."""
        t = block.type
        if t == "figure":
            if not block.anchor:
                block.anchor = unique_slug(block.figure_id or block.label or "figure", self._used_anchors)
        elif t == "table":
            if not block.anchor:
                block.anchor = unique_slug(block.table_id or block.label or "table", self._used_anchors)
        elif t == "equation":
            if not block.anchor:
                # Equation numbers arrive parenthesized ("(3)"); anchors must
                # be bare digits ("eq-3") to stay valid HTML ids.
                num = str(block.equation_number or "").strip().strip("()[]")
                if not num:
                    # Unnumbered equation: fall back to a document-ordinal id
                    # ("eq-?" is not a valid HTML id).
                    self._eq_counter += 1
                    while f"eq-{self._eq_counter}" in self._used_anchors:
                        self._eq_counter += 1
                    num = str(self._eq_counter)
                block.anchor = unique_slug(block.label or f"eq-{num}", self._used_anchors)
        elif t == "algorithm":
            if not block.anchor:
                num = str(block.algorithm_number or "").strip()
                base = block.label or (f"alg-{num}" if num else "algorithm")
                block.anchor = unique_slug(base, self._used_anchors)
        elif t == "heading":
            if not block.anchor and block.label:
                block.anchor = unique_slug(block.label, self._used_anchors)
        elif t == "blockquote":
            for child in block.blocks:
                self._anchor_block(child)
        elif t == "list":
            for item in block.items:
                for child in item:
                    self._anchor_block(child)

    def _repoint_section_fragments(self, doc: DocumentIR) -> None:
        """Repoint raw arXiv section fragments (``S4``, ``S4.SS1``) to real anchors.

        The builder's old positional guess (``section-4-1``) never matched the
        slugified anchors actually emitted, so in-document section links were
        dead. Figure/table/algorithm fragments are repointed separately, via
        the ``_label_to_anchor`` map in :meth:`_repoint_fragment_links` (the
        builder keeps the raw ``S2.F1`` fragment — LaTeXML ids are
        section-local, caption numbers are global).

        On the HTML path sections carry the ar5iv element id ("S4") as their
        anchor — that id is the *authoritative* key for a link written
        against the source numbering. Positional keys (computed after any
        section filtering) only fill gaps; otherwise deleting one section
        would shift every later link onto the wrong anchor.
        """
        fragment_map: dict[str, str] = {}
        positional: dict[str, str] = {}

        def index_section(section: SectionIR, path: list[int]) -> None:
            key = ".".join(("S" if i == 0 else "SS" if i == 1 else "SSS") + str(n) for i, n in enumerate(path))
            if section.anchor:
                if _SECTION_FRAGMENT_RE.match(section.anchor):
                    fragment_map[section.anchor] = section.anchor
                else:
                    positional[key] = section.anchor
            for j, child in enumerate(section.children, start=1):
                index_section(child, [*path, j])

        for i, section in enumerate(doc.sections, start=1):
            index_section(section, [i])

        for key, anchor in positional.items():
            fragment_map.setdefault(key, anchor)

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
        return unique_slug(base, self._used_anchors)

    def _claim_and_anchor(self, block, block_id: str) -> None:
        """Register *block_id* as claimed and give *block* a unique anchor.

        The anchor is only rewritten when it mirrors the id (or was empty);
        label-based anchors set by builders are preserved as-is — and
        registered as used so a later auto anchor cannot collide with them.
        """
        self._claimed.add(block_id)
        current = getattr(block, "anchor", None)
        if not current or current == block_id:
            block.anchor = self._unique_anchor(block_id)
        else:
            self._used_anchors.add(current)
        label = getattr(block, "label", None)
        if label and block.anchor:
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
