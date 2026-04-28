"""Numbering pass: assign sequential numbers to figures, tables, equations, and algorithms."""

from __future__ import annotations

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass


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
