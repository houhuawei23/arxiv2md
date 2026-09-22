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
from arxiv2md_beta.ir.builders._math_norm import TAG_RE
from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.emitters.base import IREmitter
from arxiv2md_beta.ir.emitters.escapes import (
    code_fence,
    escape_html_attr,
    escape_line_start,
    escape_math_pipes,
    escape_md_text,
    escape_pipe_cell,
    escape_url,
    inline_code_delims,
)
from arxiv2md_beta.ir.inlines import ImageRefIR, InlineUnion

# ── inline delimiter map ──────────────────────────────────────────────

_EMPHASIS_DELIMITERS: dict[str, str] = {
    # Italic emphasis is intentionally plain text: ar5iv wraps large spans in
    # <em>/<span class="ltx_font_italic">, and the resulting *...* runs (nested
    # inside **bold** as ***, and around inline math/underscores) break Markdown
    # rendering. Only **bold** survives.
    "italic": "",
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

# Text inline that only separates two adjacent citations (", ", "; ").
_CITE_SEP_RE = re.compile(r"^[\s,;]*$")


def _is_citation(il) -> bool:
    """Whether *il* is a numeric citation link (``[N]`` / ``[N](#ref-N)``)."""
    return (
        getattr(il, "type", "") == "link"
        and getattr(il, "kind", "") == "citation"
        and bool(_CITATION_NUM_RE.match(getattr(il, "target_id", "") or ""))
    )


def _next_is_citation(items: list, idx: int) -> bool:
    """Whether the first non-separator inline after *idx* is a numeric cite.

    Used to decide if a whitespace/comma text inline joins two citations of
    one run (drop it) or ends the run (keep the prose text verbatim).
    """
    for il in items[idx + 1 :]:
        if il.type == "text" and _CITE_SEP_RE.match(il.text):
            continue
        return _is_citation(il)
    return False


def _blockquote_lines(text: str) -> str:
    """Prefix every line with ``> `` so multi-line captions stay one block."""
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.split("\n"))


def _a_id(anchor: str) -> str:
    """``<a id="…">`` line with the id attribute HTML-escaped (audit5 I-11)."""
    return f'<a id="{escape_html_attr(anchor)}"></a>'


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

        # Front matter (title-block figures etc. collected by the builders;
        # empty for most papers, so this adds nothing when unused)
        if doc.front_matter:
            parts.append(self._emit_blocks(doc.front_matter))
            parts.append("")

        # Abstract
        if doc.abstract:
            parts.append("## Abstract")
            parts.append("")
            parts.append(self._emit_blocks(doc.abstract))
            parts.append("")

        # Sections
        for section in doc.sections:
            parts.append(self._emit_section(section))

        return _post_process("\n".join(parts))

    # ── Section ────────────────────────────────────────────────────────

    def _emit_section(self, section: SectionIR) -> str:
        parts: list[str] = []

        # Anchor
        if section.anchor:
            parts.append(_a_id(section.anchor))
        elif section.struct_id:
            parts.append(_a_id(section.struct_id))

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
            # A first line starting with Markdown block syntax would render
            # as a fake heading/list/quote (audit5 G2-2)
            return escape_line_start(self._emit_inlines(getattr(block, "inlines", [])))
        elif t == "heading":
            level = getattr(block, "level", 2)
            text = self._emit_inlines(getattr(block, "inlines", []))
            anchor = getattr(block, "anchor", None)
            prefix = f"{_a_id(anchor)}\n\n" if anchor else ""
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
            fence = code_fence(block.text)
            return f"{fence}{lang}\n{block.text}\n{fence}"
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
        # ar5iv wraps citation groups in literal bracket text inlines —
        # "[" / "]" (natbib \cite style) or "(" / ")" (\citep style) —
        # which would otherwise yield "[[57]]" or "((11, 12))" once the
        # emitter adds its own parens. Drop them when they directly bound
        # a citation; the open/close pairing flag guards genuine prose
        # parens around non-citation content.
        cleaned: list = []
        bracket_open = False
        for i, il in enumerate(inlines):
            prev_is_cite = i > 0 and _is_citation(inlines[i - 1])
            next_is_cite = i + 1 < len(inlines) and _is_citation(inlines[i + 1])
            if il.type == "text":
                if il.text in ("[", "(") and next_is_cite:
                    bracket_open = True
                    continue
                if il.text in ("]", ")") and prev_is_cite and bracket_open:
                    bracket_open = False
                    continue
                # Separators sit inside a citation run and must not reset
                # the pending-open flag; any other text does.
                if not _CITE_SEP_RE.match(il.text):
                    bracket_open = False
            cleaned.append(il)

        # Merge runs of adjacent numeric citations ("[30] , [52]") into a
        # single parenthesised group "(30, 52)" instead of "[[30], [52]]".
        parts: list[str] = []
        run: list = []

        def flush_run() -> None:
            if not run:
                return
            matches = [_CITATION_NUM_RE.match(il.target_id or "") for il in run]
            nums = [n.strip() for m in matches if m for n in m.group(1).split(",")]
            if all(matches) and nums:
                if self.linked_citations:
                    parts.append("(" + ", ".join(f"[{n}](#ref-{n})" for n in nums) + ")")
                else:
                    parts.append(f"({', '.join(nums)})")
            else:
                parts.extend(self._emit_inline(il) for il in run)
            run.clear()

        for i, il in enumerate(cleaned):
            if _is_citation(il):
                run.append(il)
            elif run and il.type == "text" and _CITE_SEP_RE.match(il.text) and _next_is_citation(cleaned, i):
                continue  # separator between two citations of the same run
            else:
                flush_run()
                parts.append(self._emit_inline(il))
        flush_run()
        return "".join(parts)

    def _emit_inline(self, inline) -> str:
        t = inline.type

        if t == "text":
            return inline.text
        elif t == "emphasis":
            # Empty emphasis spans (ar5iv emits bare <span class="ltx_font_italic">
            # wrappers) would otherwise serialize as a stray '**'/'****' line.
            if not inline.inlines:
                return ""
            style = inline.style
            if style == "code":
                # Backtick content breaks a single-` span; delimiters are
                # content-dependent (audit5 G2-3)
                inner = self._emit_inlines(inline.inlines)
                d, c = inline_code_delims(inner)
                return f"{d}{inner}{c}"
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
                # Strip one pair of literal brackets if the builder/HTML left
                # them in the text — the emitter adds its own below.
                if cite_text.startswith("[") and cite_text.endswith("]"):
                    cite_text = cite_text[1:-1].strip()
                if inline.target_id and self.linked_citations:
                    return f"[{escape_md_text(cite_text)}](#{escape_url(inline.target_id)})"
                return f"[{escape_md_text(cite_text)}]"
            if inline.kind == "internal" and inline.target_id:
                return f"[{escape_md_text(text)}](#{escape_url(inline.target_id)})"
            elif inline.url:
                return f"[{escape_md_text(text)}]({escape_url(inline.url)})"
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
            return f"![{escape_md_text(alt)}]({escape_url(src)})"
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
            lines.append(_a_id(fid))
            lines.append("")

        # Images — every path routes alt/src through the escape policies
        # (escapes.py): GFM syntax position for the single-image case, raw
        # HTML attributes otherwise. The LaTeX builder derives alt from
        # caption plain text with no bracket cleaning, so an unescaped ']'
        # here breaks the image (audit4 B1).
        images = fig.images
        if fig.grid:
            lines.append(self._emit_figure_grid(fig.grid))
        elif len(images) == 1:
            img = images[0]
            alt = img.alt or ""
            src = img.src or ""
            lines.append(f"![{escape_md_text(alt)}]({escape_url(src)})")
        elif len(images) > 1:
            lines.append('<div align="center">')
            width = "45%" if len(images) == 2 else f"{max(14, min(90 // len(images), 45))}%"
            for img in images:
                # HTML attr context: escape_html_attr only — escape_md_text
                # would display literal backslashes (audit5 I-1)
                alt = escape_html_attr(img.alt or "Figure panel")
                src = escape_html_attr(escape_url(img.src or ""))
                w = escape_html_attr(str(img.width)) if img.width else width
                w_attr = f' width="{w}"'
                lines.append(f'  <img src="{src}"{w_attr} alt="{alt}" />')
            lines.append("</div>")

        # Caption
        caption = self._emit_inlines(fig.caption)
        if caption:
            lines.append("")
            lines.append(_blockquote_lines(caption))

        return "\n".join(lines)

    def _emit_figure_grid(self, grid: list[list[list[InlineUnion]]]) -> str:
        """Render a table-layout figure as raw HTML, preserving row/column grid.

        Each cell is a list of inlines: ``ImageRefIR`` nodes render as ``<img>``
        (scaled to the cell via ``width="100%"``), everything else through the
        normal inline emitter. Kept as HTML so the layout survives in Markdown
        viewers that render inline HTML (same convention as the flat multi-image
        strip).
        """
        rows: list[str] = []
        for row in grid:
            cells: list[str] = []
            for cell in row:
                parts: list[str] = []
                for inline in cell:
                    if isinstance(inline, ImageRefIR):
                        src = escape_html_attr(escape_url(inline.src or ""))
                        alt = escape_html_attr(inline.alt or "Figure panel")
                        parts.append(f'<img src="{src}" width="100%" alt="{alt}" />')
                    else:
                        # Raw text inside an HTML <td>: escape markup
                        # characters so source <> cannot break the table
                        # (audit5 I-12)
                        parts.append(escape_html_attr(self._emit_inlines([inline])))
                cells.append(f"<td>{''.join(parts)}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return "\n".join(["<table>", *rows, "</table>"])

    def _emit_table(self, tbl: TableIR) -> str:
        lines: list[str] = []

        # Anchor
        tid = tbl.table_id or tbl.anchor
        if tid:
            lines.append(_a_id(tid))
            lines.append("")

        # Headers & rows. Cell content is flattened to one line: a literal
        # newline would tear the pipe table apart, so BreakIR-style breaks
        # become <br>. Cells render through _cell_inlines: math latex gets
        # '|' → \vert on copies so the pipe escaping below cannot rewrite it.
        headers = [_escape_pipe_cell(_cell_text(self._emit_inlines(self._cell_inlines(h)))) for h in tbl.headers]
        rows = [
            [_escape_pipe_cell(_cell_text(self._emit_inlines(self._cell_inlines(c)))) for c in row] for row in tbl.rows
        ]

        if not headers and not rows:
            return ""

        max_cols = max([len(headers)] + [len(r) for r in rows])

        if headers:
            # Header row + separator
            lines.append("| " + " | ".join(headers + [""] * (max_cols - len(headers))) + " |")
            lines.append("| " + " | ".join("---" for _ in range(max_cols)) + " |")
            for row in rows:
                lines.append("| " + " | ".join(row + [""] * (max_cols - len(row))) + " |")
        else:
            # Headerless table: Markdown requires a header line, so emit a
            # blank one — every data row is preserved.
            lines.append("| " + " | ".join("" for _ in range(max_cols)) + " |")
            lines.append("| " + " | ".join("---" for _ in range(max_cols)) + " |")
            for row in rows:
                lines.append("| " + " | ".join(row + [""] * (max_cols - len(row))) + " |")

        # Caption
        caption = self._emit_inlines(tbl.caption)
        if caption:
            lines.append("")
            lines.append(_blockquote_lines(caption))

        return "\n".join(lines)

    # ── Table cell rendering ───────────────────────────────────────────

    def _cell_inlines(self, inlines: list) -> list:
        r"""Cell-scoped copies of *inlines* whose math latex is table-safe.

        The pipe-table renderer escapes every unescaped ``|`` in a cell; inside
        ``$…$`` that rewrite is semantic damage (KaTeX renders ``\|`` as ‖,
        turning a conditional probability into a norm). Replacing the character
        with the equivalent ``\vert`` on *copies* keeps both the math and the
        table intact — the emitter never mutates the IR it renders.
        """
        return [self._cell_safe_copy(il) for il in inlines]

    def _cell_safe_copy(self, il):
        t = getattr(il, "type", "")
        if t == "math":
            if "|" in il.latex:
                return il.model_copy(update={"latex": escape_math_pipes(il.latex)})
            return il
        if t in ("emphasis", "superscript", "subscript") and getattr(il, "inlines", None):
            return il.model_copy(update={"inlines": self._cell_inlines(il.inlines)})
        return il

    def _emit_equation(self, eq: EquationIR) -> str:
        parts: list[str] = []
        anchor = eq.anchor
        if anchor:
            parts.append(_a_id(anchor))
            parts.append("")
        num = eq.equation_number
        latex = eq.latex
        if num:
            # ar5iv equation numbers arrive parenthesized, e.g. "(1)"; \tag adds
            # its own parens during rendering, so strip surrounding ()/[].
            num_str = num.strip().strip("()")
            if num_str:
                # The source may already carry \tag{...}; the extracted number
                # is authoritative and the rendered math must keep exactly one
                # tag (audit5 G1-2).
                latex = TAG_RE.sub("", latex).strip()
                parts.append(f"$$\n{latex} \\tag{{{num_str}}}\n$$")
            else:
                parts.append(f"$$\n{latex}\n$$")
        else:
            parts.append(f"$$\n{latex}\n$$")
        return "\n".join(parts)

    def _emit_list(self, lst: ListIR) -> str:
        lines: list[str] = []
        for idx, item_blocks in enumerate(lst.items):
            lines.extend(self._emit_list_item(item_blocks, lst.ordered, 0, idx, start=lst.start))
        return "\n".join(lines)

    def _emit_list_item(
        self,
        item_blocks: list,
        ordered: bool,
        indent: int,
        index: int = 0,
        indent_width: int = 0,
        start: int | None = None,
    ) -> list[str]:
        # A nested item's indentation must reach the parent's content column
        # (CommonMark): 2 spaces under "- ", 3 under "1. ", 4 under "10. ".
        # A fixed 2-space indent used to demote nested ordered lists to
        # paragraph continuation text.
        prefix = " " * indent_width
        # start offsets the numbering so a continued list ("<ol start=3>") does
        # not restart at 1 (audit5 G1-3); None means the default first number.
        number = (start if start is not None else 1) + index
        marker = f"{prefix}{number}. " if ordered else f"{prefix}- "
        continuation_indent = " " * len(marker)
        # Block-level content inside a list item must be indented enough for
        # standard Markdown parsers to recognise it as part of the item. We use
        # at least 4 spaces per nesting level (or one past the marker width,
        # whichever is larger) and preserve that indentation through the
        # downstream display-math formatter.
        block_indent = " " * max(indent_width + len(marker) + 1, 4 * (indent + 1))
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
                    lines.extend(
                        self._emit_list_item(
                            nested_item,
                            blk.ordered,
                            indent + 1,
                            nested_idx,
                            indent_width=indent_width + len(marker),
                            start=blk.start,
                        )
                    )
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
            lines.append(_a_id(anchor))
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
    """Return True for blocks that should sit on their own line inside a list item.

    Headings count: flattening one into the item line emits a literal
    "# Foo" (audit5 I-13).
    """
    t = getattr(block, "type", None)
    return t in ("equation", "figure", "table", "code", "blockquote", "rule", "heading")


def _wrap_line(line: str, continuation_indent: str, width: int = 100) -> list[str]:
    """Wrap a long line, indenting continuation lines to preserve list alignment.

    Tokens wider than a whole line (long URLs, unspaced formulas, entire CJK
    paragraphs) used to overflow as one enormous line; they are now
    hard-broken at the width boundary — the first chunk on the un-indented
    line, later chunks under the continuation indent.
    """
    if len(line) <= width:
        return [line]
    # Continuation lines carry the indent, so their content budget shrinks.
    cont_room = max(1, width - len(continuation_indent))
    lines: list[str] = []
    current = ""
    for word in line.split(" "):
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}" if current else word
            continue
        if current:
            lines.append(current)
            current = ""
        room = width if not lines else cont_room
        if len(word) <= room:
            current = f"{continuation_indent}{word}" if lines else word
            continue
        # Hard-break a token too wide for even a fresh line.
        while len(word) > room:
            lines.append((f"{continuation_indent}" if lines else "") + word[:room])
            word = word[room:]
            room = cont_room
        current = f"{continuation_indent}{word}" if lines else word
    if current:
        lines.append(current)
    return lines


# ── Post-processing ────────────────────────────────────────────────────


def _post_process(md: str) -> str:
    """Clean up the rendered markdown.

    Blank-line collapsing happens once, in ``finalize_markdown`` (the last
    postprocess step); doing it here too was a redundant full-text scan.
    """
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
    return escape_pipe_cell(text)
