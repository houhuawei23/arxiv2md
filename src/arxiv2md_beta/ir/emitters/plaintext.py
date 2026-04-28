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
    RawBlockIR,
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
    RawInlineIR,
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
        t = block.type
        if t == "paragraph":
            return self._emit_inlines(block.inlines)  # type: ignore[union-attr]
        elif t == "heading":
            b = block  # type: ignore[assignment]
            prefix = "#" * min(b.level, 6)
            return f"{prefix} {self._emit_inlines(b.inlines)}"
        elif t == "figure":
            b = block  # type: ignore[assignment]
            cap = self._emit_inlines(b.caption)
            return f"[Figure: {cap}]" if cap else "[Figure]"
        elif t == "table":
            b = block  # type: ignore[assignment]
            lines: list[str] = []
            if b.headers:
                lines.append(" | ".join(self._emit_inlines(h) for h in b.headers))
            for row in b.rows:
                lines.append(" | ".join(self._emit_inlines(cell) for cell in row))
            cap = self._emit_inlines(b.caption)
            result = "\n".join(lines)
            if cap:
                result += f"\n[Table: {cap}]"
            return result
        elif t == "equation":
            b = block  # type: ignore[assignment]
            label = f" ({b.equation_number})" if b.equation_number else ""
            return f"[Equation{label}]: {b.latex}"
        elif t == "list":
            b = block  # type: ignore[assignment]
            items: list[str] = []
            for i, item in enumerate(b.items):
                prefix = f"{i + 1}." if b.ordered else "-"
                content = " ".join(self._emit_blocks(paras) for paras in [item])
                items.append(f"{prefix} {content}")
            return "\n".join(items)
        elif t == "code":
            b = block  # type: ignore[assignment]
            lang = f" ({b.language})" if b.language else ""
            return f"[Code{lang}]:\n{b.text}"
        elif t == "blockquote":
            b = block  # type: ignore[assignment]
            inner = self._emit_blocks(b.blocks)
            return "\n".join(f"> {line}" for line in inner.split("\n"))
        elif t == "algorithm":
            b = block  # type: ignore[assignment]
            cap = self._emit_inlines(b.caption)
            steps = self._emit_blocks(b.steps)
            return f"[Algorithm: {cap}]\n{steps}"
        elif t == "rule":
            return "---"
        elif t == "raw_block":
            b = block  # type: ignore[assignment]
            return b.content
        return ""

    def _emit_inlines(self, inlines: list[InlineUnion]) -> str:
        parts: list[str] = []
        for il in inlines:
            emitted = self._emit_inline(il)
            if emitted:
                parts.append(emitted)
        return "".join(parts)

    def _emit_inline(self, il: InlineUnion) -> str:
        t = il.type
        if t == "text":
            return il.text  # type: ignore[union-attr]
        elif t == "math":
            m = il  # type: ignore[assignment]
            return f"${m.latex}$" if not m.display else f"$$\n{m.latex}\n$$"
        elif t == "emphasis":
            e = il  # type: ignore[assignment]
            inner = self._emit_inlines(e.inlines)
            if e.style == "code":
                return f"`{inner}`"
            return inner
        elif t == "link":
            l = il  # type: ignore[assignment]
            inner = self._emit_inlines(l.inlines)
            return inner if inner else (l.url or "")
        elif t == "image_ref":
            img = il  # type: ignore[assignment]
            return img.alt or "[image]"
        elif t in ("superscript", "subscript"):
            s = il  # type: ignore[assignment]
            return self._emit_inlines(s.inlines)
        elif t == "break":
            return "\n"
        elif t == "raw_inline":
            return il.content  # type: ignore[union-attr]
        return ""
