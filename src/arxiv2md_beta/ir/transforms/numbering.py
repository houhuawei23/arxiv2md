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
                SectionNumberingPass._number_sections(sec.children, None)
                continue

            counter[-1] += 1
            number_str = ".".join(str(n) for n in counter)
            sec.struct_id = f"sec_{number_str.replace('.', '_')}"
            sec.title = f"{number_str} {sec.title}"

            # Descend into children.
            SectionNumberingPass._number_sections(sec.children, counter)
        counter.pop()


class NumberingPass(IRPass):
    """Assign sequential IDs to numbered elements.

    Walks the document and assigns ``figure_id``, ``table_id``,
    ``equation_number``, and ``algorithm_number`` fields.
    """

    name = "numbering"
    description = "Assign sequential numbers to figures, tables, equations, and algorithms."

    def run(self, doc: DocumentIR) -> DocumentIR:
        ctx = {"figure": 0, "table": 0, "equation": 0, "algorithm": 0}

        for block in doc.abstract:
            self._number_blocks([block], ctx)
        for section in doc.sections:
            self._number_section(section, ctx)

        return doc

    def _number_section(self, section: SectionIR, ctx: dict) -> None:
        self._number_blocks(section.blocks, ctx)
        for child in section.children:
            self._number_section(child, ctx)

    def _number_blocks(self, blocks: list, ctx: dict) -> None:
        for block in blocks:
            t = block.type
            if t == "figure":
                need_assign = not block.figure_id
                if need_assign:
                    ctx["figure"] += 1
                    block.figure_id = f"figure-{ctx['figure']}"
                if not block.anchor:
                    block.anchor = block.figure_id
            elif t == "table":
                need_assign = not block.table_id
                if need_assign:
                    ctx["table"] += 1
                    block.table_id = f"table-{ctx['table']}"
                if not block.anchor:
                    block.anchor = block.table_id
            elif t == "equation":
                need_assign = not block.equation_number
                if need_assign:
                    ctx["equation"] += 1
                    block.equation_number = f"({ctx['equation']})"
                if not block.anchor:
                    block.anchor = f"eq-{ctx['equation']}"
            elif t == "algorithm":
                need_assign = not block.algorithm_number
                if need_assign:
                    ctx["algorithm"] += 1
                    block.algorithm_number = str(ctx["algorithm"])
                if not block.anchor:
                    block.anchor = f"algorithm-{ctx['algorithm']}"
            elif t == "list":
                for item in block.items:
                    self._number_blocks(item, ctx)
            elif t == "blockquote":
                self._number_blocks(block.blocks, ctx)
