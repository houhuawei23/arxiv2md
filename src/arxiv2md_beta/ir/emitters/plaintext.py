"""Plain text emitter: DocumentIR → plain text (for token counting, search, etc.)."""

from __future__ import annotations

from arxiv2md_beta.ir.blocks import (
    AlgorithmIR,
    BlockQuoteIR,
    BlockUnion,
    CodeIR,
    EquationIR,
    FigureIR,
    HeadingIR,
    ListIR,
    ParagraphIR,
    RuleIR,
    TableIR,
)
from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.emitters.base import IREmitter
from arxiv2md_beta.ir.inlines import (
    BreakIR,
    EmphasisIR,
    ImageRefIR,
    InlineUnion,
    LinkIR,
    MathIR,
    SubscriptIR,
    SuperscriptIR,
    TextIR,
)


class PlainTextEmitter(IREmitter):
    """Emit :class:`DocumentIR` as plain text with minimal formatting."""

    format_name = "plaintext"

    def emit(self, doc: DocumentIR) -> str:
        parts: list[str] = []
        if doc.abstract:
            parts.append(self._emit_blocks(doc.abstract))
        for section in doc.sections:
            parts.append(self._emit_section(section))
        if doc.bibliography:
            parts.append(self._emit_blocks(doc.bibliography))
        return "\n\n".join(parts)

    def _emit_section(self, section: SectionIR) -> str:
        parts: list[str] = []
        if section.title:
            prefix = "#" * min(section.level, 6)
            parts.append(f"{prefix} {section.title}")
        if section.blocks:
            parts.append(self._emit_blocks(section.blocks))
        for child in section.children:
            parts.append(self._emit_section(child))
        return "\n\n".join(parts)

    def _emit_blocks(self, blocks: list[BlockUnion]) -> str:
        result: list[str] = []
        for block in blocks:
            emitted = self._emit_block(block)
            if emitted:
                result.append(emitted)
        return "\n\n".join(result)

    def _emit_block(self, block: BlockUnion) -> str:
        if isinstance(block, ParagraphIR):
            return self._emit_inlines(block.inlines)
        elif isinstance(block, HeadingIR):
            prefix = "#" * min(block.level, 6)
            return f"{prefix} {self._emit_inlines(block.inlines)}"
        elif isinstance(block, FigureIR):
            cap = self._emit_inlines(block.caption)
            return f"[Figure: {cap}]" if cap else "[Figure]"
        elif isinstance(block, TableIR):
            lines: list[str] = []
            if block.headers:
                lines.append(" | ".join(self._emit_inlines(h) for h in block.headers))
            for row in block.rows:
                lines.append(" | ".join(self._emit_inlines(cell) for cell in row))
            cap = self._emit_inlines(block.caption)
            result = "\n".join(lines)
            if cap:
                result += f"\n[Table: {cap}]"
            return result
        elif isinstance(block, EquationIR):
            label = f" ({block.equation_number})" if block.equation_number else ""
            return f"[Equation{label}]: {block.latex}"
        elif isinstance(block, ListIR):
            items: list[str] = []
            for i, item in enumerate(block.items):
                prefix = f"{i + 1}." if block.ordered else "-"
                content = " ".join(self._emit_blocks(paras) for paras in [item])
                items.append(f"{prefix} {content}")
            return "\n".join(items)
        elif isinstance(block, CodeIR):
            lang = f" ({block.language})" if block.language else ""
            return f"[Code{lang}]:\n{block.text}"
        elif isinstance(block, BlockQuoteIR):
            inner = self._emit_blocks(block.blocks)
            return "\n".join(f"> {line}" for line in inner.split("\n"))
        elif isinstance(block, AlgorithmIR):
            cap = self._emit_inlines(block.caption)
            steps = self._emit_blocks(block.steps)
            return f"[Algorithm: {cap}]\n{steps}"
        elif isinstance(block, RuleIR):
            return "---"
        else:
            # RawBlockIR and any future block types fall back to content.
            return block.content

    def _emit_inlines(self, inlines: list[InlineUnion]) -> str:
        parts: list[str] = []
        for il in inlines:
            emitted = self._emit_inline(il)
            if emitted:
                parts.append(emitted)
        return "".join(parts)

    def _emit_inline(self, il: InlineUnion) -> str:
        if isinstance(il, TextIR):
            return il.text
        elif isinstance(il, MathIR):
            return f"${il.latex}$" if not il.display else f"$$\n{il.latex}\n$$"
        elif isinstance(il, EmphasisIR):
            inner = self._emit_inlines(il.inlines)
            if il.style == "code":
                return f"`{inner}`"
            return inner
        elif isinstance(il, LinkIR):
            inner = self._emit_inlines(il.inlines)
            return inner if inner else (il.url or "")
        elif isinstance(il, ImageRefIR):
            return il.alt or "[image]"
        elif isinstance(il, SuperscriptIR | SubscriptIR):
            return self._emit_inlines(il.inlines)
        elif isinstance(il, BreakIR):
            return "\n"
        else:
            # RawInlineIR and any future inline types fall back to content.
            return il.content
