"""Markdown emitter: serialize a :class:`DocumentIR` to a Markdown string."""

from __future__ import annotations

from arxiv2md_beta.ir.blocks import (
    AlgorithmIR,
    BlockQuoteIR,
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
    LinkIR,
    MathIR,
    RawInlineIR,
    SubscriptIR,
    SuperscriptIR,
    TextIR,
)

# ── inline delimiter map ──────────────────────────────────────────────

_EMPHASIS_DELIMITERS: dict[str, str] = {
    "italic": "*",
    "bold": "**",
    "code": "`",
    "underline": "<u>",
    "strikethrough": "~~",
}

_EMPHASIS_CLOSERS: dict[str, str] = {
    "underline": "</u>",
}


class MarkdownEmitter(IREmitter):
    """Serialize a :class:`DocumentIR` to GitHub-flavoured Markdown."""

    format_name = "markdown"

    def emit(self, doc: DocumentIR) -> str:
        parts: list[str] = []

        # Abstract
        if doc.abstract:
            parts.append("## Abstract")
            parts.append("")
            parts.append(self._emit_blocks(doc.abstract))
            parts.append("")

        # Sections
        for section in doc.sections:
            parts.append(self._emit_section(section))

        # Bibliography
        if doc.bibliography:
            parts.append(self._emit_blocks(doc.bibliography))

        return _post_process("\n".join(parts))

    # ── Section ────────────────────────────────────────────────────────

    def _emit_section(self, section: SectionIR) -> str:
        parts: list[str] = []

        # Anchor
        if section.anchor:
            parts.append(f'<a id="{section.anchor}"></a>')
        elif section.struct_id:
            parts.append(f'<a id="{section.struct_id}"></a>')

        # Heading
        hashes = "#" * max(1, min(6, section.level))
        parts.append(f"{hashes} {section.title}")

        # Blocks
        if section.blocks:
            parts.append("")
            parts.append(self._emit_blocks(section.blocks))

        # Child sections
        for child in section.children:
            parts.append("")
            parts.append(self._emit_section(child))

        return "\n".join(parts)

    # ── Blocks ─────────────────────────────────────────────────────────

    def _emit_blocks(self, blocks: list) -> str:
        return "\n\n".join(
            b for b in (self._emit_block(blk) for blk in blocks) if b
        )

    def _emit_block(self, block) -> str:
        t = block.type

        if t == "paragraph":
            return self._emit_inlines(getattr(block, "inlines", []))
        elif t == "heading":
            level = getattr(block, "level", 2)
            text = self._emit_inlines(getattr(block, "inlines", []))
            anchor = getattr(block, "anchor", None)
            prefix = f'<a id="{anchor}"></a>\n\n' if anchor else ""
            return f"{prefix}{'#' * level} {text}"
        elif t == "figure":
            return self._emit_figure(block)
        elif t == "table":
            return self._emit_table(block)
        elif t == "equation":
            return self._emit_equation(block)
        elif t == "list":
            return self._emit_list(block)
        elif t == "code":
            lang = getattr(block, "language", "") or ""
            return f"```{lang}\n{block.text}\n```"
        elif t == "blockquote":
            inner = self._emit_blocks(getattr(block, "blocks", []))
            return "\n".join(f"> {line}" for line in inner.split("\n"))
        elif t == "algorithm":
            return self._emit_algorithm(block)
        elif t == "rule":
            return "---"
        elif t == "raw_block":
            return block.content
        return ""

    # ── Inlines ────────────────────────────────────────────────────────

    def _emit_inlines(self, inlines: list) -> str:
        return "".join(self._emit_inline(il) for il in inlines)

    def _emit_inline(self, inline) -> str:
        t = inline.type

        if t == "text":
            return inline.text
        elif t == "emphasis":
            style = inline.style
            d = _EMPHASIS_DELIMITERS.get(style, "")
            c = _EMPHASIS_CLOSERS.get(style, d)
            return f"{d}{self._emit_inlines(inline.inlines)}{c}"
        elif t == "link":
            text = self._emit_inlines(inline.inlines)
            if inline.kind == "citation" and inline.target_id:
                return f"[{text}](#{inline.target_id})"
            elif inline.kind == "internal" and inline.target_id:
                return f"[{text}](#{inline.target_id})"
            elif inline.url:
                return f"[{text}]({inline.url})"
            return text
        elif t == "math":
            if inline.display:
                return f"$$\n{inline.latex}\n$$"
            return f"${inline.latex}$"
        elif t == "image_ref":
            alt = inline.alt or ""
            src = inline.src or ""
            w = f' width="{inline.width}"' if inline.width else ""
            h = f' height="{inline.height}"' if inline.height else ""
            return f"![{alt}]({src}{w}{h})"
        elif t == "superscript":
            return f"^{self._emit_inlines(inline.inlines)}"
        elif t == "subscript":
            return f"_{self._emit_inlines(inline.inlines)}"
        elif t == "break":
            return "\n"
        elif t == "raw_inline":
            return inline.content
        return ""

    # ── Complex block renderers ────────────────────────────────────────

    def _emit_figure(self, fig: FigureIR) -> str:
        lines: list[str] = []

        # Anchor
        fid = fig.figure_id or fig.anchor
        if fid:
            lines.append(f'<a id="{fid}"></a>')
            lines.append("")

        # Images
        images = fig.images
        if len(images) == 1:
            img = images[0]
            alt = img.alt or ""
            src = img.src or ""
            lines.append(f"![{alt}]({src})")
        elif len(images) > 1:
            lines.append('<div align="center">')
            width = "45%" if len(images) == 2 else f"{max(14, min(90 // len(images), 45))}%"
            for img in images:
                alt = img.alt or "Figure panel"
                src = img.src or ""
                w_attr = f' width="{img.width}"' if img.width else f' width="{width}"'
                lines.append(f'  <img src="{src}"{w_attr} alt="{alt}" />')
            lines.append("</div>")

        # Caption
        caption = self._emit_inlines(fig.caption)
        if caption:
            lines.append("")
            lines.append(f"> {caption}")

        return "\n".join(lines)

    def _emit_table(self, tbl: TableIR) -> str:
        lines: list[str] = []

        # Anchor
        tid = tbl.table_id or tbl.anchor
        if tid:
            lines.append(f'<a id="{tid}"></a>')
            lines.append("")

        # Headers & rows
        headers = [self._emit_inlines(h) for h in tbl.headers]
        rows = [[self._emit_inlines(c) for c in row] for row in tbl.rows]

        all_rows = [headers] + rows if headers else rows
        if not all_rows:
            return ""

        max_cols = max(len(r) for r in all_rows)
        normalized = [r + [""] * (max_cols - len(r)) for r in all_rows]

        # Header row + separator
        lines.append("| " + " | ".join(normalized[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in normalized[0]) + " |")
        for row in normalized[1:]:
            lines.append("| " + " | ".join(row) + " |")

        # Caption
        caption = self._emit_inlines(tbl.caption)
        if caption:
            lines.append("")
            lines.append(f"> {caption}")

        return "\n".join(lines)

    def _emit_equation(self, eq: EquationIR) -> str:
        parts: list[str] = []
        anchor = eq.anchor
        if anchor:
            parts.append(f'<a id="{anchor}"></a>')
            parts.append("")
        num = eq.equation_number
        latex = eq.latex
        if num:
            parts.append(f"$$\n{latex} \\tag{{{num}}}\n$$")
        else:
            parts.append(f"$$\n{latex}\n$$")
        return "\n".join(parts)

    def _emit_list(self, lst: ListIR) -> str:
        lines: list[str] = []
        for idx, item_blocks in enumerate(lst.items):
            lines.extend(self._emit_list_item(item_blocks, lst.ordered, 0, idx))
        return "\n".join(lines)

    def _emit_list_item(self, item_blocks: list, ordered: bool, indent: int, index: int = 0) -> list[str]:
        prefix = "  " * indent
        lines: list[str] = []

        # Split into block items and nested lists
        text_blocks = []
        for blk in item_blocks:
            if hasattr(blk, "type") and blk.type == "list":
                # Nested list — render after the text
                text = " ".join(
                    self._emit_block(b) for b in text_blocks
                ).strip()
                marker = f"{prefix}{index + 1}. " if ordered else f"{prefix}- "
                lines.append(f"{marker}{text}" if text else f"{marker}")
                text_blocks = []
                for nested_idx, nested_item in enumerate(blk.items):
                    lines.extend(self._emit_list_item(nested_item, blk.ordered, indent + 1, nested_idx))
            else:
                text_blocks.append(blk)

        if text_blocks:
            text = " ".join(self._emit_block(b) for b in text_blocks).strip()
            marker = f"{prefix}{index + 1}. " if ordered else f"{prefix}- "
            lines.insert(0, f"{marker}{text}" if text else f"{marker}")

        return lines

    def _emit_algorithm(self, alg: AlgorithmIR) -> str:
        lines: list[str] = []
        anchor = alg.anchor
        if anchor:
            lines.append(f'<a id="{anchor}"></a>')
            lines.append("")
        caption = self._emit_inlines(alg.caption)
        if caption:
            lines.append(f"**{caption}**")
        for step in alg.steps:
            step_text = self._emit_block(step)
            if step_text:
                lines.append(step_text)
        return "\n".join(lines)


# ── Post-processing ────────────────────────────────────────────────────


def _post_process(md: str) -> str:
    """Clean up the rendered markdown."""
    import re

    # Collapse 3+ blank lines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)

    # Remove trailing whitespace on each line
    md = "\n".join(line.rstrip() for line in md.split("\n"))

    # Trim leading/trailing blank lines
    md = md.strip()

    return md
