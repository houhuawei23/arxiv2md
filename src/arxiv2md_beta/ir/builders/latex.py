"""LaTeX builder: Pandoc JSON AST → DocumentIR."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, cast

from arxiv2md_beta.ir.blocks import (
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
from arxiv2md_beta.ir.builders.base import IRBuilder
from arxiv2md_beta.ir.core import SourceLoc
from arxiv2md_beta.ir.document import AuthorIR, DocumentIR, PaperMetadata, SectionIR
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
from arxiv2md_beta.ir.resolvers import ImageResolver

_SHARED_SOURCE = SourceLoc(parser="latex")


def _pandoc_attrs_id(attrs: list[Any]) -> str:
    """Extract the element id from Pandoc Attr."""
    if isinstance(attrs, list) and len(attrs) > 0 and isinstance(attrs[0], str):
        return cast(str, attrs[0])
    return ""


def _pandoc_attrs_classes(attrs: list[Any]) -> list[str]:
    """Extract CSS classes from Pandoc Attr."""
    if isinstance(attrs, list) and len(attrs) > 1 and isinstance(attrs[1], list):
        return [str(c) for c in attrs[1]]
    return []


class LaTeXBuilder(IRBuilder):
    """Build a :class:`DocumentIR` from LaTeX source via Pandoc JSON AST.

    Parameters
    ----------
    image_map : dict[str, Path] | None
        Mapping from LaTeX image paths/names to local processed image paths.
        Keys are the original ``\\includegraphics`` path, filename, or stem;
        values are the local :class:`Path` to the processed image file.
    image_resolver : ImageResolver | None
        Unified resolver (preferred).  If provided, *image_map* is ignored.
    """

    def __init__(
        self,
        image_map: dict[str, Path] | None = None,
        image_resolver: ImageResolver | None = None,
    ) -> None:
        self.image_map: dict[str, Path] = image_map or {}
        self._image_resolver = image_resolver or ImageResolver(
            path_map=self.image_map,
        )
        # Footnote state — flushed at block boundaries
        self._pending_footnotes: deque[tuple[int, list[dict]]] = deque()
        self._footnote_counter: int = 0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build(self, source: Any, **kwargs: Any) -> DocumentIR:
        """Parse LaTeX *source* and return a :class:`DocumentIR`.

        Parameters
        ----------
        source : str
            Expanded LaTeX content (after ``\\input``/``\\include`` resolution).
        **kwargs : Any
            * ``arxiv_id``: str – arXiv identifier.
            * ``title``: str | None – pre-extracted title.
            * ``authors``: list[str] | None – pre-extracted author names.
            * ``abstract``: str | None – pre-extracted abstract text.
            * ``base_dir``: Path | None – directory for relative-path resolution
              (forwarded to pandoc).

        Returns
        -------
        DocumentIR
        """
        arxiv_id = kwargs.get("arxiv_id", "")
        title: str | None = kwargs.get("title")
        authors: list[str] = kwargs.get("authors", [])
        abstract_text: str | None = kwargs.get("abstract")

        tex_content = cast(str, source)

        # Convert LaTeX → Pandoc JSON AST
        try:
            import pypandoc
        except ImportError:
            from arxiv2md_beta.latex.parser import ParserNotAvailableError
            raise ParserNotAvailableError(
                "pypandoc is required for LaTeX parsing. "
                "Install it with: pip install pypandoc"
            )

        try:
            json_str = pypandoc.convert_text(
                tex_content, "json", format="latex", extra_args=["--wrap=none"]
            )
        except RuntimeError as e:
            raise RuntimeError(f"Failed to convert LaTeX to Pandoc AST: {e}") from e
        except OSError as e:
            raise RuntimeError(
                f"Failed to convert LaTeX (pandoc not found?): {e}"
            ) from e

        ast = json.loads(json_str)
        blocks: list[dict] = ast.get("blocks", [])

        # Try to read metadata from Pandoc meta if not provided
        meta = ast.get("meta", {})
        if not title:
            title = self._meta_to_text(meta.get("title"))
        if not authors:
            extracted_authors = self._meta_to_authors(meta.get("author"))
            if extracted_authors:
                authors = extracted_authors
        if not abstract_text:
            abstract_text = self._meta_to_text(meta.get("abstract"))

        # Split blocks into abstract, body, bibliography
        abstract_blocks: list[BlockUnion] = []
        body_blocks: list[dict] = list(blocks)
        bib_blocks: list[BlockUnion] = []

        # Simple heuristic: if there's a Header containing "References" or
        # "Bibliography", split there.
        bib_start_idx: int | None = None
        for i, blk in enumerate(blocks):
            if blk.get("t") == "Header":
                h_inlines = self._inlines_from_pandoc(blk.get("c", [None, [], []])[2])
                title_text = self._inlines_to_plain_text(h_inlines).lower().strip()
                if title_text in (
                    "references", "bibliography", "reference",
                    "literature cited", "works cited",
                ):
                    bib_start_idx = i
                    break

        if bib_start_idx is not None:
            body_blocks = list(blocks[:bib_start_idx])
            bib_blocks = self._blocks_from_pandoc(
                blocks[bib_start_idx:], section_id="bib"
            )

        # Build sections from body blocks
        sections = self._build_sections(body_blocks)

        author_irs = [AuthorIR(name=a) for a in authors]
        return DocumentIR(
            metadata=PaperMetadata(
                arxiv_id=arxiv_id,
                title=title,
                authors=author_irs,
                abstract_text=abstract_text,
                parser="latex",
            ),
            abstract=abstract_blocks,
            sections=sections,
            bibliography=bib_blocks,
        )

    # ------------------------------------------------------------------
    # Pandoc meta → plain text
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_to_text(meta_value: Any) -> str | None:
        """Convert a Pandoc MetaValue (JSON form) to plain text."""
        if not meta_value:
            return None
        # MetaInlines: {"t": "MetaInlines", "c": [inlines...]}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaInlines":
            return LaTeXBuilder._raw_inlines_to_text(meta_value.get("c", []))
        # MetaBlocks: {"t": "MetaBlocks", "c": [blocks...]}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaBlocks":
            parts: list[str] = []
            for blk in meta_value.get("c", []):
                txt = LaTeXBuilder._blocks_to_plain_text([blk])
                if txt:
                    parts.append(txt)
            return " ".join(parts) if parts else None
        # MetaString: {"t": "MetaString", "c": "string"}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaString":
            return str(meta_value.get("c", ""))
        if isinstance(meta_value, str):
            return meta_value
        return str(meta_value)

    @staticmethod
    def _meta_to_authors(meta_value: Any) -> list[str]:
        """Convert a Pandoc MetaValue for authors to a list of name strings."""
        if not meta_value:
            return []
        # MetaList: {"t": "MetaList", "c": [...]}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaList":
            return [
                name
                for item in meta_value.get("c", [])
                if (name := LaTeXBuilder._meta_to_text(item))
            ]
        text = LaTeXBuilder._meta_to_text(meta_value)
        if not text:
            return []
        # Split by \and
        import re
        return [a.strip() for a in re.split(r"\\and|\\AND", text) if a.strip()]

    # ------------------------------------------------------------------
    # Block-level conversion
    # ------------------------------------------------------------------

    def _build_sections(self, blocks: list[dict]) -> list[SectionIR]:
        """Split a flat list of Pandoc blocks into a :class:`SectionIR` tree."""
        sections: list[SectionIR] = []
        stack: list[dict] = []  # stack of (level, SectionIR)

        current_blocks: list[dict] = []
        current_level: int | None = None
        current_header: tuple[int, str, str] | None = None  # level, title, anchor

        def _flush_section() -> SectionIR | None:
            nonlocal current_blocks, current_header
            if current_header is None and not current_blocks:
                return None
            level, title, anchor = current_header or (1, "", "")
            sec = SectionIR(
                title=title or "",
                level=level,
                anchor=anchor if anchor else None,
                blocks=self._blocks_from_pandoc(current_blocks, section_id=""),
            )
            current_blocks = []
            current_header = None
            return sec

        for blk in blocks:
            t = blk.get("t")
            if t == "Header":
                c = blk.get("c", [1, ["", [], []], []])
                level = c[0] if isinstance(c, list) and len(c) > 0 else 1
                anchor = _pandoc_attrs_id(c[1]) if len(c) > 1 else ""
                inlines = self._inlines_from_pandoc(c[2]) if len(c) > 2 else []
                title = self._inlines_to_plain_text(inlines)

                # Flush previous section
                sec = _flush_section()
                if sec:
                    # Pop stack until we find a parent
                    while stack and stack[-1][0] >= current_level if current_level is not None else True:
                        stack.pop()
                    if stack:
                        stack[-1][1].children.append(sec)
                    else:
                        sections.append(sec)

                current_header = (level, title, anchor)
                current_level = level
            else:
                current_blocks.append(blk)

        # Flush final section
        sec = _flush_section()
        if sec:
            while stack:
                stack.pop()
            if stack:
                stack[-1][1].children.append(sec)
            else:
                sections.append(sec)

        # If there are no sections but we have blocks, wrap in a default section
        if not sections and current_blocks:
            # Shouldn't happen, but handle
            pass

        # Build hierarchy from level information
        sections = self._build_section_hierarchy(sections)
        return sections

    @staticmethod
    def _build_section_hierarchy(flat_sections: list[SectionIR]) -> list[SectionIR]:
        """Build parent-child relationships from section levels."""
        result: list[SectionIR] = []
        stack: list[SectionIR] = []

        for sec in flat_sections:
            while stack and stack[-1].level >= sec.level:
                stack.pop()
            if stack:
                stack[-1].children.append(sec)
            else:
                result.append(sec)
            stack.append(sec)

        return result

    def _blocks_from_pandoc(
        self, blocks: list[dict], section_id: str = ""
    ) -> list[BlockUnion]:
        """Convert a list of Pandoc block dicts to IR blocks."""
        result: list[BlockUnion] = []
        order = 0
        for blk in blocks:
            ir_block = self._block_from_pandoc(blk, section_id, order)
            if ir_block is not None:
                if isinstance(ir_block, list):
                    for b in ir_block:
                        b.order_index = order
                        b.section_id = section_id
                        order += 1
                        result.append(b)
                else:
                    ir_block.order_index = order
                    ir_block.section_id = section_id
                    order += 1
                    result.append(ir_block)
            else:
                order += 1
            # Flush any pending footnotes generated by inline elements
            # in this block.
            while self._pending_footnotes:
                fn_num, fn_raw_blocks = self._pending_footnotes.popleft()
                # Convert footnote blocks without recursion to avoid
                # re-entrant flush issues.
                fn_ir_blocks: list[BlockUnion] = []
                for fb in fn_raw_blocks:
                    fb_ir = self._block_from_pandoc(fb, section_id, order)
                    if fb_ir is not None:
                        if isinstance(fb_ir, list):
                            fn_ir_blocks.extend(fb_ir)
                        else:
                            fn_ir_blocks.append(fb_ir)
                # Marker paragraph: [^N]
                marker = ParagraphIR(
                    inlines=[TextIR(text=f"[^{fn_num}]")],
                    source=_SHARED_SOURCE,
                    section_id=section_id,
                    order_index=order,
                )
                result.append(marker)
                order += 1
                for b in fn_ir_blocks:
                    b.section_id = section_id
                    b.order_index = order
                    order += 1
                    result.append(b)
        return result

    def _block_from_pandoc(
        self, blk: dict, section_id: str = "", order: int = 0
    ) -> BlockUnion | list[BlockUnion] | None:
        """Convert a single Pandoc block dict to an IR block."""
        t = blk.get("t", "")
        c = blk.get("c", [])

        if t == "Para":
            inlines = self._inlines_from_pandoc(c) if isinstance(c, list) else []
            return ParagraphIR(
                inlines=inlines, source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "Plain":
            inlines = self._inlines_from_pandoc(c) if isinstance(c, list) else []
            return ParagraphIR(
                inlines=inlines, source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "Header":
            c_list = c if isinstance(c, list) else [1, ["", [], []], []]
            level = c_list[0] if len(c_list) > 0 else 1
            anchor = _pandoc_attrs_id(c_list[1])
            inlines = self._inlines_from_pandoc(c_list[2] if len(c_list) > 2 else [])
            return HeadingIR(
                level=level, inlines=inlines,
                anchor=anchor if anchor else None,
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "CodeBlock":
            c_list = c if isinstance(c, list) else [["", [], []], "", ""]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            lang = str(c_list[1]) if len(c_list) > 1 else ""
            code = str(c_list[2]) if len(c_list) > 2 else ""
            anchor = _pandoc_attrs_id(attrs)
            classes = _pandoc_attrs_classes(attrs)
            language = lang if lang else (classes[0] if classes else None)
            return CodeIR(
                language=language, text=code,
                anchor=anchor if anchor else None,
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "BlockQuote":
            inner = self._blocks_from_pandoc(
                c if isinstance(c, list) else [], section_id
            )
            return BlockQuoteIR(
                blocks=inner, source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "OrderedList":
            list_attrs = c[0] if isinstance(c, list) and len(c) > 0 else [1, {}, {}]
            items_list = c[1] if isinstance(c, list) and len(c) > 1 else c if isinstance(c, list) else []
            items_structure: list[list[BlockUnion]] = []
            for item in items_list:
                item_blocks = self._blocks_from_pandoc(
                    item if isinstance(item, list) else [], section_id
                )
                items_structure.append(item_blocks)
            return ListIR(
                ordered=True, items=items_structure,
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "BulletList":
            items_list = c if isinstance(c, list) else []
            items_structure: list[list[BlockUnion]] = []
            for item in items_list:
                item_blocks = self._blocks_from_pandoc(
                    item if isinstance(item, list) else [], section_id
                )
                items_structure.append(item_blocks)
            return ListIR(
                ordered=False, items=items_structure,
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "Table":
            return self._build_table_from_pandoc(c, section_id, order)
        elif t == "Figure":
            return self._build_figure_from_pandoc(c, section_id, order)
        elif t in ("HorizontalRule",):
            return RuleIR(
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "Div":
            attrs = c[0] if isinstance(c, list) and len(c) > 0 else ["", [], []]
            inner_blocks = c[1] if isinstance(c, list) and len(c) > 1 else []
            anchor = _pandoc_attrs_id(attrs)
            div_blocks = self._blocks_from_pandoc(
                inner_blocks if isinstance(inner_blocks, list) else [], section_id
            )
            if anchor:
                for b in div_blocks:
                    if not b.anchor:
                        b.anchor = anchor
            return div_blocks if len(div_blocks) > 0 else None
        elif t == "RawBlock":
            fmt = str(c[0]) if isinstance(c, list) and len(c) > 0 else "latex"
            content = str(c[1]) if isinstance(c, list) and len(c) > 1 else ""
            return RawBlockIR(
                format=fmt if fmt in ("html", "latex") else "latex",
                content=content,
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "LineBlock":
            inlines_list = c if isinstance(c, list) else []
            all_inlines: list[InlineUnion] = []
            for line in inlines_list:
                line_inlines = self._inlines_from_pandoc(
                    line if isinstance(line, list) else []
                )
                if all_inlines:
                    all_inlines.append(BreakIR())
                all_inlines.extend(line_inlines)
            return ParagraphIR(
                inlines=all_inlines, source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )
        elif t == "Null":
            return None
        else:
            # Unknown block type: preserve as RawBlock
            return RawBlockIR(
                format="latex",
                content=json.dumps(blk),
                source=_SHARED_SOURCE,
                section_id=section_id, order_index=order,
            )

    # ------------------------------------------------------------------
    # Inline-level conversion
    # ------------------------------------------------------------------

    def _inlines_from_pandoc(self, inlines: list) -> list[InlineUnion]:
        """Convert a list of Pandoc inline dicts to IR inlines."""
        result: list[InlineUnion] = []
        for il in inlines:
            if not isinstance(il, dict):
                continue  # skip strings or other non-dict items
            converted = self._inline_from_pandoc(il)
            if converted is not None:
                if isinstance(converted, list):
                    result.extend(converted)
                else:
                    result.append(converted)
        return result

    def _inline_from_pandoc(self, il: dict) -> InlineUnion | list[InlineUnion] | None:
        """Convert a single Pandoc inline dict to an IR inline."""
        t = il.get("t", "")
        c = il.get("c", [])

        if t == "Str":
            return TextIR(text=str(c) if isinstance(c, str) else str(c))
        elif t == "Space":
            return TextIR(text=" ")
        elif t == "SoftBreak":
            return TextIR(text=" ")
        elif t == "LineBreak":
            return BreakIR()
        elif t == "Emph":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="italic", inlines=inner)
        elif t == "Strong":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="bold", inlines=inner)
        elif t == "Underline":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="underline", inlines=inner)
        elif t == "Strikeout":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="strikethrough", inlines=inner)
        elif t == "Superscript":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return SuperscriptIR(inlines=inner)
        elif t == "Subscript":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return SubscriptIR(inlines=inner)
        elif t == "SmallCaps":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="italic", inlines=inner)
        elif t == "Code":
            c_list = c if isinstance(c, list) else [["", [], []], ""]
            text = str(c_list[1]) if len(c_list) > 1 else ""
            return EmphasisIR(style="code", inlines=[TextIR(text=text)])
        elif t == "Math":
            c_list = c if isinstance(c, list) else [{"t": "InlineMath"}, ""]
            mathtype = c_list[0] if len(c_list) > 0 else {}
            latex = str(c_list[1]) if len(c_list) > 1 else ""
            if isinstance(mathtype, dict):
                display = mathtype.get("t") == "DisplayMath"
            else:
                display = False
            return MathIR(latex=latex, display=display)
        elif t == "RawInline":
            fmt = str(c[0]) if isinstance(c, list) and len(c) > 0 else "latex"
            content = str(c[1]) if isinstance(c, list) and len(c) > 1 else ""
            return RawInlineIR(
                format=fmt if fmt in ("html", "latex") else "latex",
                content=content,
            )
        elif t == "Link":
            c_list = c if isinstance(c, list) else [["", [], []], [], ["", ""]]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            inner = self._inlines_from_pandoc(
                c_list[1] if len(c_list) > 1 else []
            )
            target = c_list[2] if len(c_list) > 2 else ["", ""]
            url = str(target[0]) if isinstance(target, list) and len(target) > 0 else ""
            anchor = _pandoc_attrs_id(attrs)
            # Determine link kind
            kind: str = "external"
            if url.startswith("#"):
                kind = "internal"
                if "cite" in url.lower() or "ref" in url.lower():
                    kind = "citation"
            elif not url:
                kind = "internal"
            return LinkIR(
                url=url if url else None,
                inlines=inner,
                kind=kind,  # type: ignore[arg-type]
                target_id=anchor if anchor else None,
            )
        elif t == "Image":
            c_list = c if isinstance(c, list) else [["", [], []], [], ["", ""]]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            alt_inlines = self._inlines_from_pandoc(
                c_list[1] if len(c_list) > 1 else []
            )
            target = c_list[2] if len(c_list) > 2 else ["", ""]
            src = str(target[0]) if isinstance(target, list) and len(target) > 0 else ""
            alt = self._inlines_to_plain_text(alt_inlines)
            anchor = _pandoc_attrs_id(attrs)

            # Resolve via image_map
            local_src = self._resolve_image_src(src)
            return ImageRefIR(
                src=local_src,
                alt=alt,
            )
        elif t == "Quoted":
            c_list = c if isinstance(c, list) else [{"t": "DoubleQuote"}, []]
            inner = self._inlines_from_pandoc(
                c_list[1] if len(c_list) > 1 else []
            )
            # Add quote marks around the inlines
            qt = c_list[0] if len(c_list) > 0 else {}
            if isinstance(qt, dict) and qt.get("t") == "DoubleQuote":
                left, right = "\u201c", "\u201d"
            else:
                left, right = "\u2018", "\u2019"
            return [TextIR(text=left)] + inner + [TextIR(text=right)]
        elif t == "Cite":
            c_list = c if isinstance(c, list) else [[], []]
            inner = self._inlines_from_pandoc(
                c_list[1] if len(c_list) > 1 else []
            )
            citations = c_list[0] if len(c_list) > 0 else []
            # Extract citation IDs and build superscript markers
            citation_ids: list[str] = []
            if isinstance(citations, list):
                for cit in citations:
                    if isinstance(cit, dict):
                        cid = cit.get("citationId", "")
                        if cid:
                            citation_ids.append(str(cid))
            if inner:
                # Preserve surrounding text, append citation markers
                if citation_ids:
                    markers = "[" + ", ".join(citation_ids) + "]"
                    return inner + [SuperscriptIR(inlines=[TextIR(text=markers)])]
                return inner
            if citation_ids:
                markers = "[" + ", ".join(citation_ids) + "]"
                return SuperscriptIR(inlines=[TextIR(text=markers)])
            return None
        elif t == "Note":
            # Footnote: Pandoc Note contains [Blocks]
            note_blocks = c if isinstance(c, list) else []
            self._footnote_counter += 1
            fn_num = self._footnote_counter
            self._pending_footnotes.append((fn_num, note_blocks))
            return SuperscriptIR(inlines=[TextIR(text=str(fn_num))])
        elif t == "Span":
            c_list = c if isinstance(c, list) else [["", [], []], []]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            inner = self._inlines_from_pandoc(
                c_list[1] if len(c_list) > 1 else []
            )
            anchor = _pandoc_attrs_id(attrs)
            classes = _pandoc_attrs_classes(attrs)
            if anchor:
                inner.insert(0, RawInlineIR(format="html", content=f'<a id="{anchor}"></a>'))
            return inner
        else:
            return None

    # ------------------------------------------------------------------
    # Figure / Table builders
    # ------------------------------------------------------------------

    def _build_figure_from_pandoc(
        self, c: Any, section_id: str, order: int
    ) -> BlockUnion | None:
        """Build a FigureIR from Pandoc Figure AST.

        Pandoc Figure (≥ 1.23): ``Figure Attr Caption [Body]``
        where *Caption* is ``[ShortCaption | null, [Blocks]]`` and
        *Body* is a list of blocks.
        """
        c_list = c if isinstance(c, list) else [["", [], []], [None, []], []]
        attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
        caption_data = c_list[1] if len(c_list) > 1 else [None, []]
        body_blocks = c_list[2] if len(c_list) > 2 else []
        anchor = _pandoc_attrs_id(attrs)

        # Extract caption inlines from Caption structure
        caption_inlines: list[InlineUnion] = []
        if isinstance(caption_data, list) and len(caption_data) >= 2:
            # caption_data[1] is a list of blocks (usually [Plain])
            cap_blocks = caption_data[1] if caption_data[1] else []
            if isinstance(cap_blocks, list):
                for cb in cap_blocks:
                    if isinstance(cb, dict) and cb.get("t") in ("Plain", "Para"):
                        caption_inlines.extend(
                            self._inlines_from_pandoc(
                                cb.get("c", []) if isinstance(cb.get("c"), list) else []
                            )
                        )

        images: list[ImageRefIR] = []
        for blk in body_blocks if isinstance(body_blocks, list) else []:
            if isinstance(blk, dict) and blk.get("t") in ("Plain", "Para"):
                for il in (blk.get("c", []) if isinstance(blk.get("c"), list) else []):
                    if isinstance(il, dict) and il.get("t") == "Image":
                        img_ir = self._inline_from_pandoc(il)
                        if isinstance(img_ir, ImageRefIR):
                            images.append(img_ir)

        return FigureIR(
            images=images,
            caption=caption_inlines,
            figure_id=anchor if anchor else None,
            anchor=anchor if anchor else None,
            source=_SHARED_SOURCE,
            section_id=section_id,
            order_index=order,
        )

    def _build_table_from_pandoc(
        self, c: Any, section_id: str, order: int
    ) -> BlockUnion | None:
        """Build a TableIR from Pandoc Table AST.

        Pandoc Table (≥ 1.23): ``Table Attr Caption [ColSpec] TableHead [TableBody] TableFoot``
        """
        c_list = c if isinstance(c, list) else [["", [], []], [None, []], [], ["", [], [], []], [], ["", [], []]]
        attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
        caption_data = c_list[1] if len(c_list) > 1 else [None, []]
        # c_list[2] = ColSpec (ignored)
        head = c_list[3] if len(c_list) > 3 else ["", [], []]
        body = c_list[4] if len(c_list) > 4 else []
        foot = c_list[5] if len(c_list) > 5 else ["", [], []]
        anchor = _pandoc_attrs_id(attrs)

        # Extract caption inlines from Caption structure: [ShortCaption, [Blocks]]
        caption_inlines: list[InlineUnion] = []
        if isinstance(caption_data, list) and len(caption_data) >= 2:
            cap_blocks = caption_data[1] if caption_data[1] else []
            if isinstance(cap_blocks, list):
                for cb in cap_blocks:
                    if isinstance(cb, dict) and cb.get("t") in ("Plain", "Para"):
                        caption_inlines.extend(
                            self._inlines_from_pandoc(
                                cb.get("c", []) if isinstance(cb.get("c"), list) else []
                            )
                        )

        # Head: [head_attr, [head_rows]]
        headers: list[list[InlineUnion]] = []
        head_rows: list = []
        if isinstance(head, list) and len(head) >= 2 and isinstance(head[1], list):
            head_rows = head[1]
        for row_data in head_rows:
            row_cells = self._extract_table_row_cells(row_data)
            if row_cells:
                headers.append(row_cells)

        # Body: list of table bodies, each has [body_attr, row_count, colspecs, [rows]]
        rows: list[list[list[InlineUnion]]] = []
        if isinstance(body, list):
            for body_part in body:
                if isinstance(body_part, dict) and body_part.get("t") == "TableBody":
                    body_c = body_part.get("c", [])
                    body_rows = body_c[3] if isinstance(body_c, list) and len(body_c) >= 4 else []
                    if isinstance(body_rows, list):
                        for row_data in body_rows:
                            row_cells = self._extract_table_row_cells(row_data)
                            if row_cells:
                                rows.append(row_cells)

        # Foot: similar to head
        if isinstance(foot, list) and len(foot) >= 2 and isinstance(foot[1], list):
            for row_data in foot[1]:
                row_cells = self._extract_table_row_cells(row_data)
                if row_cells:
                    rows.append(row_cells)

        # Use first header row as headers; the rest as data
        if not headers and rows:
            headers = rows[0] if isinstance(rows[0], list) else []
            rows = rows[1:] if len(rows) > 1 else []

        return TableIR(
            headers=headers,
            rows=rows,
            caption=caption_inlines,
            anchor=anchor if anchor else None,
            table_id=anchor if anchor else None,
            source=_SHARED_SOURCE,
            section_id=section_id,
            order_index=order,
        )

    def _extract_table_row_cells(self, row_data: Any) -> list[list[InlineUnion]] | None:
        """Extract cell inlines from a Pandoc Row."""
        if isinstance(row_data, dict) and row_data.get("t") == "Row":
            row_c = row_data.get("c", [])
            # Row: [attr, [cells...]]
            cells_data = row_c[1] if isinstance(row_c, list) and len(row_c) >= 2 else []
            cells: list[list[InlineUnion]] = []
            if isinstance(cells_data, list):
                for cell in cells_data:
                    if isinstance(cell, dict) and cell.get("t") == "Cell":
                        # Cell: [attr, alignment, rowspan, colspan, [blocks]]
                        cell_c = cell.get("c", [])
                        blocks = cell_c[4] if isinstance(cell_c, list) and len(cell_c) >= 5 else []
                        cell_inlines: list[InlineUnion] = []
                        if isinstance(blocks, list):
                            for b in blocks:
                                if isinstance(b, dict) and b.get("t") in ("Plain", "Para"):
                                    cell_inlines.extend(
                                        self._inlines_from_pandoc(
                                            b.get("c", []) if isinstance(b.get("c"), list) else []
                                        )
                                    )
                        cells.append(cell_inlines)
            return cells if cells else None
        return None

    # ------------------------------------------------------------------
    # Image path resolution
    # ------------------------------------------------------------------

    def _resolve_image_src(self, src: str) -> str:
        """Resolve a LaTeX image path to a local processed image path."""
        return self._image_resolver.resolve(src)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _inlines_to_plain_text(inlines: list[InlineUnion]) -> str:
        """Extract plain text from a list of InlineIR nodes."""
        parts: list[str] = []
        for il in inlines:
            if isinstance(il, TextIR):
                parts.append(il.text)
            elif isinstance(il, MathIR):
                parts.append(f"${il.latex}$" if not il.display else f"$${il.latex}$$")
            elif isinstance(il, EmphasisIR):
                inner = LaTeXBuilder._inlines_to_plain_text(il.inlines)
                parts.append(inner)
            elif isinstance(il, LinkIR):
                inner = LaTeXBuilder._inlines_to_plain_text(il.inlines)
                parts.append(inner)
            elif isinstance(il, SuperscriptIR):
                inner = LaTeXBuilder._inlines_to_plain_text(il.inlines)
                parts.append(inner)
            elif isinstance(il, SubscriptIR):
                inner = LaTeXBuilder._inlines_to_plain_text(il.inlines)
                parts.append(inner)
            elif isinstance(il, ImageRefIR):
                parts.append(il.alt or "[image]")
            elif isinstance(il, (BreakIR, RawInlineIR)):
                pass  # skip breaks and raw
        return "".join(parts)

    @staticmethod
    def _blocks_to_plain_text(blocks: list[dict]) -> str:
        """Extract plain text from a list of Pandoc block dicts (best-effort)."""
        parts: list[str] = []
        for blk in blocks:
            t = blk.get("t", "")
            c = blk.get("c", [])
            if t in ("Para", "Plain"):
                inlines = c if isinstance(c, list) else []
                text = LaTeXBuilder._raw_inlines_to_text(inlines)
                parts.append(text)
            elif t == "Header":
                c_list = c if isinstance(c, list) else [1, ["", [], []], []]
                inlines = c_list[2] if len(c_list) > 2 else []
                text = LaTeXBuilder._raw_inlines_to_text(inlines)
                parts.append(text)
        return " ".join(parts)

    @staticmethod
    def _raw_inlines_to_text(inlines: list[dict]) -> str:
        """Quick plain-text extraction from raw Pandoc inline JSON."""
        parts: list[str] = []
        for il in inlines:
            t = il.get("t", "")
            c = il.get("c", "")
            if t == "Str":
                parts.append(str(c))
            elif t == "Space":
                parts.append(" ")
            elif t in ("Emph", "Strong", "Underline", "Strikeout", "Superscript",
                        "Subscript", "SmallCaps", "Link", "Span"):
                inner = c[1] if isinstance(c, list) and len(c) > 1 else c if isinstance(c, list) else []
                parts.append(LaTeXBuilder._raw_inlines_to_text(
                    inner if isinstance(inner, list) else []
                ))
            elif t == "Code":
                parts.append(str(c[1]) if isinstance(c, list) and len(c) > 1 else "")
            elif t == "Math":
                parts.append(str(c[1]) if isinstance(c, list) and len(c) > 1 else "")
            elif t == "Quoted":
                inner = c[1] if isinstance(c, list) and len(c) > 1 else []
                parts.append(LaTeXBuilder._raw_inlines_to_text(
                    inner if isinstance(inner, list) else []
                ))
        return "".join(parts)
