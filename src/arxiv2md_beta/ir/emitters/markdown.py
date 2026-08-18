"""Markdown emitter: serialize a :class:`DocumentIR` to a Markdown string."""

from __future__ import annotations

import re

from arxiv2md_beta.exceptions import EmitterError
from arxiv2md_beta.ir.blocks import (
    AlgorithmIR,
    EquationIR,
    FigureIR,
    ListIR,
    TableIR,
)
from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.emitters.base import IREmitter

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

# Citation target_id forms: ar5iv href "#bib.bibN" -> "ref-N"; the LaTeX
# builder joins multi-cites as a bare comma list ("35,2,5").
_CITATION_NUM_RE = re.compile(r"^(?:ref-)?(\d+(?:\s*,\s*\d+)*)$")


def _escape_md_text(text: str) -> str:
    r"""Escape characters that would break ``[text](url)`` link/image syntax."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _escape_url(url: str) -> str:
    r"""Percent-encode whitespace/parens so they cannot terminate ``(url)``."""
    return re.sub(r"([ ()])", lambda m: f"%{ord(m.group(1)):02X}", url)


def _blockquote_lines(text: str) -> str:
    """Prefix every line with ``> `` so multi-line captions stay one block."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.split("\n"))


class MarkdownEmitter(IREmitter):
    """Serialize a :class:`DocumentIR` to GitHub-flavoured Markdown."""

    format_name = "markdown"

    def __init__(
        self,
        *,
        linked_citations: bool = False,
        remove_inline_citations: bool = False,
    ) -> None:
        self.linked_citations = linked_citations
        self.remove_inline_citations = remove_inline_citations

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
        return "\n\n".join(b for b in (self._emit_block(blk) for blk in blocks) if b)

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
        raise EmitterError(f"Unhandled IR block type: {t!r}")

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
            if inline.kind == "footnote":
                # Markdown footnote reference: [^N] where N is the marker text.
                return f"[^{text}]"
            if inline.kind == "citation":
                if self.remove_inline_citations:
                    return ""
                # ar5iv encodes the bibitem position in the cite href
                # (#bib.bibN -> target_id "ref-N"). Emit the numeric form [N]
                # so citations match a numbered reference list instead of bare
                # natbib keys ("[vicuna ]" -> "[9]").
                m = _CITATION_NUM_RE.match(inline.target_id or "")
                if m:
                    nums = [n.strip() for n in m.group(1).split(",")]
                    if self.linked_citations:
                        return "".join(f"[{n}](#ref-{n})" for n in nums)
                    return f"[{','.join(nums)}]"
                # Fallback: bare key text, with trailing punctuation/whitespace
                # stripped ("vicuna ", "radford2021learning, " -> "[vicuna]").
                cite_text = text.strip().rstrip(",").rstrip(";").strip()
                if inline.target_id and self.linked_citations:
                    return f"[{_escape_md_text(cite_text)}](#{_escape_url(inline.target_id)})"
                return f"[{_escape_md_text(cite_text)}]"
            if inline.kind == "internal" and inline.target_id:
                return f"[{_escape_md_text(text)}](#{_escape_url(inline.target_id)})"
            elif inline.url:
                return f"[{_escape_md_text(text)}]({_escape_url(inline.url)})"
            return text
        elif t == "math":
            if inline.display:
                return f"$$\n{inline.latex}\n$$"
            return f"${inline.latex}$"
        elif t == "image_ref":
            alt = inline.alt or ""
            src = inline.src or ""
            # GFM image syntax has no width/height inside the URL parens;
            # appending them there makes the path part of the link target.
            return f"![{_escape_md_text(alt)}]({_escape_url(src)})"
        elif t == "superscript":
            # HTML tags render reliably; bare ^/_ prefixes collide with
            # Markdown emphasis and math shorthand.
            return f"<sup>{self._emit_inlines(inline.inlines)}</sup>"
        elif t == "subscript":
            return f"<sub>{self._emit_inlines(inline.inlines)}</sub>"
        elif t == "break":
            return "\n"
        elif t == "raw_inline":
            return inline.content
        raise EmitterError(f"Unhandled IR inline type: {t!r}")

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
            lines.append(_blockquote_lines(caption))

        return "\n".join(lines)

    def _emit_table(self, tbl: TableIR) -> str:
        lines: list[str] = []

        # Anchor
        tid = tbl.table_id or tbl.anchor
        if tid:
            lines.append(f'<a id="{tid}"></a>')
            lines.append("")

        # Headers & rows. Cell content is flattened to one line: a literal
        # newline would tear the pipe table apart, so BreakIR-style breaks
        # become <br>.
        headers = [_escape_pipe_cell(_cell_text(self._emit_inlines(h))) for h in tbl.headers]
        rows = [[_escape_pipe_cell(_cell_text(self._emit_inlines(c))) for c in row] for row in tbl.rows]

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
            lines.append(_blockquote_lines(caption))

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
            # ar5iv equation numbers arrive parenthesized, e.g. "(1)"; \tag adds
            # its own parens during rendering, so strip surrounding ()/[].
            num_str = num.strip().strip("()")
            if num_str:
                parts.append(f"$$\n{latex} \\tag{{{num_str}}}\n$$")
            else:
                parts.append(f"$$\n{latex}\n$$")
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
        marker = f"{prefix}{index + 1}. " if ordered else f"{prefix}- "
        continuation_indent = " " * len(marker)
        # Block-level content inside a list item must be indented enough for
        # standard Markdown parsers to recognise it as part of the item. We use
        # at least 4 spaces per nesting level (or one past the marker width,
        # whichever is larger) and preserve that indentation through the
        # downstream display-math formatter.
        block_indent = " " * max(len(marker) + 1, 4 * (indent + 1))
        lines: list[str] = []

        # Split into block items and nested lists, rendering block-level content
        # (equations, figures, code, etc.) on their own indented lines rather
        # than flattening them into the item's first paragraph.
        text_blocks: list = []
        for blk in item_blocks:
            if hasattr(blk, "type") and blk.type == "list":
                self._flush_list_text(text_blocks, marker, continuation_indent, lines)
                text_blocks = []
                for nested_idx, nested_item in enumerate(blk.items):
                    lines.extend(self._emit_list_item(nested_item, blk.ordered, indent + 1, nested_idx))
            elif _is_block_level_in_list(blk):
                self._flush_list_text(text_blocks, marker, continuation_indent, lines)
                text_blocks = []
                rendered = self._emit_block(blk)
                for block_line in rendered.split("\n"):
                    if block_line.strip():
                        lines.append(f"{block_indent}{block_line}")
                    else:
                        lines.append("")
            else:
                text_blocks.append(blk)

        self._flush_list_text(text_blocks, marker, continuation_indent, lines)
        return lines

    def _flush_list_text(
        self,
        text_blocks: list,
        marker: str,
        continuation_indent: str,
        lines: list[str],
    ) -> None:
        """Flatten consecutive paragraph-like blocks into one list item line."""
        if not text_blocks:
            return
        text = " ".join(self._emit_block(b) for b in text_blocks).strip()
        first_line = f"{marker}{text}" if text else marker
        # Wrap long lines so continuation lines stay aligned with the item text.
        wrapped = _wrap_line(first_line, continuation_indent)
        lines.extend(wrapped)

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


def _is_block_level_in_list(block) -> bool:
    """Return True for blocks that should sit on their own line inside a list item."""
    t = getattr(block, "type", None)
    return t in ("equation", "figure", "table", "code", "blockquote", "rule")


def _wrap_line(line: str, continuation_indent: str, width: int = 100) -> list[str]:
    """Wrap a long line, indenting continuation lines to preserve list alignment."""
    if len(line) <= width:
        return [line]
    words = line.split(" ")
    lines: list[str] = []
    current = words[0] if words else ""
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = f"{continuation_indent}{word}"
    if current:
        lines.append(current)
    return lines


# ── Post-processing ────────────────────────────────────────────────────


def _post_process(md: str) -> str:
    """Clean up the rendered markdown."""
    import re

    # Collapse 3+ blank lines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)

    # Remove trailing whitespace on each line
    md = "\n".join(line.rstrip() for line in md.split("\n"))

    # Trim leading/trailing blank lines; emit a single trailing newline
    # (POSIX text-file convention; also keeps goldens stable under
    # pre-commit's end-of-file-fixer).
    return md.strip() + "\n"


def _cell_text(rendered: str) -> str:
    """Flatten rendered cell content to a single line (newlines → ``<br>``)."""
    return rendered.replace("\n", "<br>")


def _escape_pipe_cell(text: str) -> str:
    """Escape unescaped ``|`` characters inside a pipe-table cell."""
    import re

    return re.sub(r"(?<!\\)\|", r"\\|", text)
