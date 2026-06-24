"""HTML builder: convert arXiv HTML to :class:`DocumentIR`.

Reuses the existing :mod:`arxiv2md_beta.html.parser` for metadata and section
extraction, and converts HTML elements to IR nodes via BeautifulSoup traversal.
"""

from __future__ import annotations

import ast
import base64
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

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
from arxiv2md_beta.ir.builders.base import IRBuilder
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
from arxiv2md_beta.utils.html_attrs import attr_optional, attr_str
from arxiv2md_beta.utils.html_attrs import classes as css_classes


class HTMLBuilder(IRBuilder):
    """Build a :class:`DocumentIR` from arXiv HTML.

    Parameters
    ----------
    image_map : dict[int, str] | None
        Map from figure index (0-based) to local image path.
    image_stem_map : dict[str, str] | None
        Map from TeX stem to local image path.
    image_resolver : ImageResolver | None
        Unified resolver (preferred).  If provided, *image_map* and
        *image_stem_map* are ignored.
    """

    def __init__(
        self,
        image_map: Mapping[int, Path | str] | None = None,
        image_stem_map: Mapping[str, Path | str] | None = None,
        image_resolver: ImageResolver | None = None,
    ):
        self.image_map = dict(image_map or {})
        self.image_stem_map = dict(image_stem_map or {})
        self._image_resolver = image_resolver or ImageResolver(
            index_map=self.image_map,
            stem_map=self.image_stem_map,
        )
        self._figure_counter = 0
        self._used_image_indices: set[int] = set()
        self._pending_footnotes: deque[BlockUnion] = deque()

    # ── Public API ─────────────────────────────────────────────────────

    def build(self, source: Any, **kwargs: Any) -> DocumentIR:
        """Parse HTML *source* (str, bytes, or ParsedArxivHtml) into a :class:`DocumentIR`."""
        arxiv_id = kwargs.get("arxiv_id", "unknown")
        from arxiv2md_beta.html.parser import ParsedArxivHtml

        if isinstance(source, ParsedArxivHtml):
            return self._build_from_parsed(source, arxiv_id)
        if isinstance(source, bytes):
            source = source.decode("utf-8", errors="replace")
        return self._build_from_html(source, arxiv_id)

    def _build_from_parsed(self, parsed: Any, arxiv_id: str) -> DocumentIR:
        """从已解析的 :class:`ParsedArxivHtml` 构建 IR，避免再次解析完整 HTML。."""
        from arxiv2md_beta.html.parser import (
            ParsedArxivHtml,
            _extract_sections,
        )

        assert isinstance(parsed, ParsedArxivHtml)

        authors = [AuthorIR(name=a.name, affiliations=a.affiliations) for a in parsed.authors]

        # Convert abstract HTML fragment to IR blocks
        abstract_blocks = self._html_to_blocks(parsed.abstract_html, section_id="abstract")

        # Convert front matter HTML fragment to IR blocks
        front_matter_blocks = self._html_to_blocks(parsed.front_matter_html, section_id="front_matter")

        # Convert section tree and drop leaf sections that have no content
        document_root = parsed.document_root
        assert isinstance(document_root, Tag), "parsed.document_root must be a Tag"
        section_nodes = _extract_sections(document_root)
        sections = [self._build_section(sn) for sn in section_nodes]
        sections = self._filter_empty_sections(sections)

        return DocumentIR(
            metadata=PaperMetadata(
                arxiv_id=arxiv_id,
                title=parsed.title,
                authors=authors,
                submission_date=parsed.submission_date,
                abstract_text=parsed.abstract,
                parser="html",
            ),
            abstract=abstract_blocks,
            front_matter=front_matter_blocks,
            sections=sections,
        )

    def _build_from_html(self, html: str, arxiv_id: str) -> DocumentIR:
        from arxiv2md_beta.html.parser import parse_arxiv_html

        parsed = parse_arxiv_html(html)
        return self._build_from_parsed(parsed, arxiv_id)

    # ── Section building ────────────────────────────────────────────────

    def _build_section(self, section_node: Any) -> SectionIR:
        """Convert a ``SectionNode`` into a :class:`SectionIR`."""
        # section_node is from html.parser._extract_sections
        blocks = self._html_to_blocks(section_node.html, section_id=section_node.struct_id or "")
        return SectionIR(
            title=section_node.title,
            level=min(6, max(1, section_node.level)),
            anchor=section_node.anchor,
            struct_id=section_node.struct_id,
            blocks=blocks,
            children=[self._build_section(child) for child in (section_node.children or [])],
        )

    @staticmethod
    def _filter_empty_sections(sections: list[SectionIR]) -> list[SectionIR]:
        """Recursively remove leaf sections that have no blocks.

        Headings generated by arXiv theorem/proof environments are often
        extracted as empty :class:`SectionIR` nodes (their real content is
        rendered as blocks inside the parent section).  Dropping those leaves
        avoids blank titled paragraphs in the output.
        """
        kept: list[SectionIR] = []
        for sec in sections:
            children = HTMLBuilder._filter_empty_sections(sec.children)
            if not sec.blocks and not children:
                continue
            sec.children = children
            kept.append(sec)
        return kept

    # ── HTML fragment → IR blocks ──────────────────────────────────────

    def _html_to_blocks(self, html_fragment: str | None, section_id: str = "") -> list[BlockUnion]:
        """Convert an HTML fragment string to a list of block IR nodes."""
        if not html_fragment:
            return []
        soup = BeautifulSoup(html_fragment, "html.parser")
        blocks, idx = self._children_to_blocks(soup.children, section_id, 0)
        # Flush remaining footnotes at end of fragment
        while self._pending_footnotes:
            footnote = self._pending_footnotes.popleft()
            footnote.section_id = section_id
            footnote.order_index = idx
            blocks.append(footnote)
            idx += 1
        return blocks

    def _children_to_blocks(
        self, children: Iterable[Any], section_id: str, start_idx: int
    ) -> tuple[list[BlockUnion], int]:
        """Process an iterable of BeautifulSoup nodes into IR blocks.

        Returns the list of blocks and the next available index.  Pending
        footnotes are inserted after each block that generates them, but
        remaining footnotes are *not* flushed — the caller must do that.
        """
        blocks: list[BlockUnion] = []
        idx = start_idx
        for child in children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    blocks.append(
                        ParagraphIR(
                            section_id=section_id,
                            order_index=idx,
                            inlines=[TextIR(text=text)],
                        )
                    )
                    idx += 1
                continue
            if not isinstance(child, Tag):
                continue
            result = self._tag_to_blocks(child, section_id, idx)
            if isinstance(result, list):
                blocks.extend(result)
                idx += len(result)
            elif result is not None:
                blocks.append(result)
                idx += 1
            # Insert any pending footnotes after the current block
            while self._pending_footnotes:
                footnote = self._pending_footnotes.popleft()
                footnote.section_id = section_id
                footnote.order_index = idx
                blocks.append(footnote)
                idx += 1
        return blocks, idx

    def _tag_to_blocks(self, tag: Tag, section_id: str, base_idx: int) -> list[BlockUnion] | BlockUnion | None:
        """Convert a BeautifulSoup tag to one or more block IR nodes."""
        tag_name = tag.name

        # arXiv / ar5iv code listings (must come before generic div recursion)
        if tag_name == "div" and _is_ltx_listing_container(tag):
            code = self._build_listing(tag, section_id, base_idx)
            return code if code is not None else []

        classes = set(css_classes(tag))

        # ar5iv paragraph wrappers (span.ltx_p / span.ltx_para) should be treated
        # as paragraphs so that inline math inside them is not emitted as raw HTML.
        if tag_name == "p" or (tag_name == "span" and _is_ar5iv_paragraph(classes)):
            inlines = self._tag_to_inlines(tag)
            if not inlines:
                return None
            # If the paragraph contains display math, lift those equations out as
            # block-level elements so the Markdown emitter can render them with
            # proper $$ delimiters instead of inline math breaking list layout.
            split_blocks = self._split_paragraph_inlines(inlines, section_id=section_id, base_idx=base_idx)
            if len(split_blocks) == 1:
                return split_blocks[0]
            return split_blocks

        # ar5iv lists (span.ltx_enumerate / span.ltx_itemize)
        if tag_name in ("ul", "ol") or (tag_name == "span" and _is_ar5iv_list(classes)):
            items = self._build_ar5iv_list_items(tag) if tag_name == "span" else self._build_list_items(tag)
            if not items:
                return None
            return ListIR(
                section_id=section_id,
                order_index=base_idx,
                ordered=(tag_name == "ol" or _is_ar5iv_ordered_list(classes)),
                items=items,
            )

        # ar5iv equation tables rendered as <span class="ltx_equation ltx_eqn_table">
        if tag_name in ("span", "div") and _is_equation_table(tag):
            latex = self._extract_equation_latex(tag)
            if not latex:
                return None
            return EquationIR(
                section_id=section_id,
                order_index=base_idx,
                latex=latex,
                equation_number=_extract_equation_number(tag),
            )

        # Container elements — recurse directly without re-parsing
        if tag_name in ("section", "article", "div", "span"):
            blocks, _ = self._children_to_blocks(tag.children, section_id, base_idx)
            return blocks

        # Headings
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_name[1])
            text = self._get_text(tag)
            anchor = attr_optional(tag, "id") or ""
            if not text:
                return None
            return HeadingIR(
                section_id=section_id,
                order_index=base_idx,
                level=level,
                anchor=anchor,
                inlines=[TextIR(text=text)],
            )

        # Figures
        if tag_name == "figure":
            return self._build_figure(tag, section_id, base_idx)

        # Tables
        if tag_name == "table":
            return self._build_table(tag, section_id, base_idx)

        # Blockquote
        if tag_name == "blockquote":
            inner_blocks, _ = self._children_to_blocks(tag.children, section_id, base_idx)
            if not inner_blocks:
                return None
            return BlockQuoteIR(
                section_id=section_id,
                order_index=base_idx,
                blocks=inner_blocks,
            )

        # Pre / code
        if tag_name == "pre":
            code_tag = tag.find("code")
            lang = ""
            if isinstance(code_tag, Tag):
                tag_classes = css_classes(code_tag)
                for cls in tag_classes:
                    if cls.startswith("language-"):
                        lang = cls.replace("language-", "")
                        break
                text = code_tag.get_text()
            else:
                text = tag.get_text()
            return CodeIR(
                section_id=section_id,
                order_index=base_idx,
                language=lang or None,
                text=text,
            )

        if tag_name == "code":
            return CodeIR(
                section_id=section_id,
                order_index=base_idx,
                text=tag.get_text(),
            )

        # Horizontal rule
        if tag_name == "hr":
            return RuleIR(section_id=section_id, order_index=base_idx)

        # Skip SVG elements (usually decorative/typographic renderings)
        if tag_name == "svg":
            return None

        # Line breaks at block level are layout noise; ignore them.
        if tag_name == "br":
            return None

        # Standalone math at block level
        if tag_name == "math":
            latex = _extract_math_latex(tag)
            if not latex:
                return None
            if tag.get("display") == "block":
                return EquationIR(
                    section_id=section_id,
                    order_index=base_idx,
                    latex=latex,
                )
            return ParagraphIR(
                section_id=section_id,
                order_index=base_idx,
                inlines=[MathIR(latex=latex, display=False)],
            )

        # Raw fallback
        return RawBlockIR(
            section_id=section_id,
            order_index=base_idx,
            format="html",
            content=str(tag),
        )

    # ── Inline conversion ──────────────────────────────────────────────

    def _split_paragraph_inlines(
        self,
        inlines: list[InlineUnion],
        *,
        section_id: str,
        base_idx: int,
    ) -> list[BlockUnion]:
        """Split paragraph inlines into paragraph/equation blocks.

        Display math that appears inside a paragraph wrapper is lifted to a
        block-level :class:`EquationIR` so it is rendered as display math rather
        than inline ``$$...$$`` embedded in a paragraph line.
        """
        blocks: list[BlockUnion] = []
        current: list[InlineUnion] = []

        def _flush_current() -> None:
            nonlocal current
            # Drop runs that only contain whitespace text
            if any(not (il.type == "text" and not il.text.strip()) for il in current):
                blocks.append(
                    ParagraphIR(
                        section_id=section_id,
                        order_index=base_idx + len(blocks),
                        inlines=list(current),
                    )
                )
            current = []

        for il in inlines:
            if il.type == "math" and getattr(il, "display", False):
                _flush_current()
                blocks.append(
                    EquationIR(
                        section_id=section_id,
                        order_index=base_idx + len(blocks),
                        latex=il.latex or "",
                    )
                )
            else:
                current.append(il)

        _flush_current()
        return blocks

    def _tag_to_inlines(self, tag: Tag) -> list[InlineUnion]:
        """Convert a BeautifulSoup tag's children to a list of inline IR nodes."""
        inlines: list[InlineUnion] = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                text = str(child)
                # Skip whitespace-only strings (prevents blank lines in tables/lists)
                if text and not text.strip():
                    continue
                if text:
                    inlines.append(TextIR(text=text))
            elif isinstance(child, Tag):
                il = self._tag_to_inline(child)
                if il is not None:
                    if isinstance(il, list):
                        inlines.extend(il)
                    else:
                        inlines.append(il)
        return inlines

    def _tag_to_inline(self, tag: Tag) -> InlineUnion | list[InlineUnion] | None:
        """Convert a single BeautifulSoup tag to an inline IR node."""
        tag_name = tag.name

        # Text formatting
        if tag_name in ("em", "i"):
            return EmphasisIR(
                style="italic",
                inlines=self._tag_to_inlines(tag),
            )
        if tag_name in ("strong", "b"):
            return EmphasisIR(
                style="bold",
                inlines=self._tag_to_inlines(tag),
            )
        if tag_name == "code":
            return EmphasisIR(
                style="code",
                inlines=self._tag_to_inlines(tag),
            )

        # Links
        if tag_name == "a":
            href = attr_str(tag, "href")
            text = self._get_text(tag)
            inlines = self._tag_to_inlines(tag) or [TextIR(text=text)]

            # Citation link
            if _is_citation_link(href):
                ref_anchor = _extract_citation_ref(href)
                return LinkIR(
                    kind="citation",
                    target_id=ref_anchor,
                    inlines=inlines,
                )

            # Internal link
            if href.startswith("#"):
                anchor = _map_fragment_to_anchor(href)
                return LinkIR(
                    kind="internal",
                    target_id=anchor or href[1:],
                    inlines=inlines,
                )

            return LinkIR(kind="external", url=href, inlines=inlines)

        # Inline equation tables (ar5iv sometimes places display math inside
        # paragraph spans as <table class="ltx_equation">).
        if tag_name == "table" and _is_equation_table(tag):
            latex = self._extract_equation_latex(tag)
            if latex:
                return MathIR(latex=latex, display=True)
            return None

        # Math
        if tag_name == "math":
            latex = _extract_math_latex(tag)
            is_display = tag.get("display") == "block"
            return MathIR(latex=latex, display=is_display)

        # Images
        if tag_name == "img":
            src = attr_str(tag, "src")
            alt = attr_str(tag, "alt")
            return ImageRefIR(src=src, alt=alt)

        # Superscript / Subscript
        if tag_name == "sup":
            return SuperscriptIR(inlines=self._tag_to_inlines(tag))
        if tag_name == "sub":
            return SubscriptIR(inlines=self._tag_to_inlines(tag))

        # Line break
        if tag_name == "br":
            return BreakIR()

        # Paragraph inside inline context (e.g. nested inside list items)
        if tag_name == "p":
            return self._tag_to_inlines(tag)

        # Spans/divs with inline content — recurse, with class-aware styling
        if tag_name in ("span", "cite", "label"):
            classes = " ".join(css_classes(tag))

            # Footnotes — extract marker inline, queue content as block
            if "ltx_note" in classes and "ltx_role_footnote" in classes:
                return self._process_footnote(tag)

            inlines = self._tag_to_inlines(tag)
            if "ltx_font_italic" in classes:
                return EmphasisIR(style="italic", inlines=inlines)
            if "ltx_font_bold" in classes:
                return EmphasisIR(style="bold", inlines=inlines)
            return inlines

        # Fallback: raw text
        return RawInlineIR(format="html", content=str(tag))

    def _process_footnote(self, tag: Tag) -> InlineUnion:
        """Extract footnote marker and queue content for block-level insertion."""
        # Extract marker from first <sup class="ltx_note_mark">
        mark = tag.find("sup", class_="ltx_note_mark")
        marker_text = self._get_text(mark) if isinstance(mark, Tag) else ""

        # Extract content from .ltx_note_content
        content_tag = tag.find("span", class_="ltx_note_content")
        if isinstance(content_tag, Tag):
            # Parse a copy to avoid mutating the original tree
            content_copy = BeautifulSoup(str(content_tag), "html.parser").find("span", class_="ltx_note_content")
            if isinstance(content_copy, Tag):
                # Remove inner note marks and tags so only the actual text remains
                for inner_mark in content_copy.find_all("sup", class_="ltx_note_mark"):
                    inner_mark.decompose()
                for inner_tag in content_copy.find_all("span", class_="ltx_tag_note"):
                    inner_tag.decompose()

                content_inlines = self._tag_to_inlines(content_copy)
                if content_inlines:
                    self._pending_footnotes.append(
                        BlockQuoteIR(
                            blocks=[
                                ParagraphIR(
                                    inlines=[
                                        TextIR(text=f"Footnote {marker_text}: "),
                                        *content_inlines,
                                    ]
                                )
                            ]
                        )
                    )

        return SuperscriptIR(inlines=[TextIR(text=marker_text)])

    def _get_text(self, tag: Tag) -> str:
        """Get normalized text content from a tag."""
        return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()

    def _extract_equation_latex(self, tag: Tag) -> str:
        r"""Extract LaTeX from an equation table, preferring <math> annotations.

        ar5iv renders equations as HTML text *alongside* <math> tags.
        Using ``get_text()`` would concatenate both the Unicode rendering and
        the LaTeX annotation, producing duplicated garbage.  We collect *only*
        the ``<annotation encoding="application/x-tex">`` nodes inside every
        <math> child, join them, and fall back to plain text only when no math
        annotation is present.

        If the table contains an equation number (e.g. ``<span class="ltx_tag_equation">(14)</span>``)
        and the LaTeX does not already include a ``\tag{}``, append the number.
        """
        math_tags = tag.find_all("math")
        latex_parts: list[str] = []
        for math_tag in math_tags:
            annotation = math_tag.find("annotation", attrs={"encoding": "application/x-tex"})
            if annotation and annotation.text:
                latex_parts.append(annotation.text.strip())
        latex = " ".join(latex_parts) if latex_parts else self._get_text(tag)
        latex = _normalize_math_latex(latex)
        # Strip outer display-math delimiters if present
        if latex.startswith("$$") and latex.endswith("$$"):
            latex = latex[2:-2]
        elif latex.startswith("$") and latex.endswith("$"):
            latex = latex[1:-1]

        # Strip any existing \tag{...} from the LaTeX annotation; the
        # authoritative paper number lives in the HTML table cell and is
        # extracted separately via _extract_equation_number().
        latex = re.sub(r"\\tag\{[^}]*\}", "", latex).strip()
        return latex

    # ── Complex block builders ─────────────────────────────────────────

    def _build_figure(self, tag: Tag, section_id: str, base_idx: int) -> BlockUnion | None:
        """Build a FigureIR or AlgorithmIR from a <figure> tag."""
        tag_classes = " ".join(css_classes(tag))

        # Caption
        caption_tag = tag.find("figcaption")
        if not isinstance(caption_tag, Tag):
            caption_tag = tag.find("span", class_=re.compile(r"ltx_caption"))
        if isinstance(caption_tag, Tag):
            caption = self._tag_to_inlines(caption_tag)
            caption_text = self._get_text(caption_tag)
        else:
            caption = []
            caption_text = ""

        # Extract figure number from caption
        fig_id = _extract_figure_id(caption_text) or f"figure-{self._figure_counter}"

        # Algorithm figure
        if "ltx_float_algorithm" in tag_classes or "ltx_algorithm" in tag_classes:
            alg_num = _extract_algorithm_number(caption_text)
            return AlgorithmIR(
                section_id=section_id,
                order_index=base_idx,
                anchor=fig_id,
                caption=caption,
                algorithm_number=alg_num,
            )

        # Table figure
        if "ltx_table" in tag_classes:
            inner_table = tag.find("table")
            if isinstance(inner_table, Tag):
                return self._build_table(inner_table, section_id, base_idx)

        # Image figure (default) — resolve local image paths
        imgs = tag.find_all("img")
        figure_index = self._figure_counter + 1  # 1-based for image_map lookup
        images = [
            ImageRefIR(
                src=self._resolve_image_src(img, figure_index),
                alt=attr_str(img, "alt"),
            )
            for img in imgs
        ]

        self._figure_counter += 1
        return FigureIR(
            section_id=section_id,
            order_index=base_idx,
            figure_id=fig_id,
            anchor=fig_id,
            images=images,
            caption=caption,
            kind="image",
        )

    def _resolve_image_src(self, img_tag: Tag, figure_index: int) -> str:
        """Resolve an <img> src to a local path via :class:`ImageResolver`."""
        src = attr_str(img_tag, "src")
        if not src:
            return src
        return self._image_resolver.resolve(src, figure_index=figure_index)

    def _build_table(self, tag: Tag, section_id: str, base_idx: int) -> BlockUnion | None:
        """Build a TableIR or EquationIR from a <table> tag."""
        classes = " ".join(css_classes(tag))

        # Equation tables
        if _EQUATION_TABLE_RE.search(classes):
            # Prefer LaTeX from <math> annotations; fall back to plain text
            latex = self._extract_equation_latex(tag)
            if not latex:
                return None
            return EquationIR(
                section_id=section_id,
                order_index=base_idx,
                latex=latex,
                equation_number=_extract_equation_number(tag),
            )

        # Data tables
        headers, rows = _extract_table_data(tag, self._tag_to_inlines)
        if not rows and not headers:
            return None

        # Caption
        caption_tag = tag.find("caption")
        if isinstance(caption_tag, Tag):
            caption = self._tag_to_inlines(caption_tag)
            caption_text = self._get_text(caption_tag)
        else:
            caption = []
            caption_text = ""
        table_id = _extract_table_id(caption_text)

        return TableIR(
            section_id=section_id,
            order_index=base_idx,
            table_id=table_id,
            headers=headers,
            rows=rows,
            caption=caption,
        )

    def _build_listing(self, tag: Tag, section_id: str, base_idx: int) -> CodeIR | None:
        """Build a CodeIR from an arXiv ``div.ltx_listing``.

        Prefer the base64 payload embedded in ``ltx_listing_data``; otherwise
        reconstruct the listing from ``ltx_listingline`` rows.
        """
        cls = " ".join(css_classes(tag))
        data = tag.find("div", class_=re.compile(r"ltx_listing_data"))
        if isinstance(data, Tag):
            a = data.find("a", href=re.compile(r"^data:text/plain"))
            if isinstance(a, Tag) and attr_str(a, "href"):
                decoded = _decode_data_plain_href(str(a["href"]))
                if decoded is not None:
                    lang = _extract_listing_language(cls)
                    lang = _normalize_listing_language(lang, decoded)
                    return CodeIR(
                        section_id=section_id,
                        order_index=base_idx,
                        language=lang,
                        text=decoded.rstrip(),
                    )

        lines_out: list[str] = []
        for line in tag.find_all("div", class_=re.compile(r"ltx_listingline"), recursive=False):
            line_num = line.find("span", class_=re.compile(r"ltx_tag_listingline"))
            if line_num:
                line_num.decompose()
            text = line.get_text(separator="").rstrip()
            lines_out.append(text)

        if lines_out:
            body = "\n".join(lines_out).rstrip()
            lang = _extract_listing_language(cls)
            lang = _normalize_listing_language(lang, body)
            return CodeIR(
                section_id=section_id,
                order_index=base_idx,
                language=lang,
                text=body,
            )
        return None

    def _build_list_items(self, tag: Tag) -> list[list[BlockUnion]]:
        """Build list items from a <ul> or <ol> tag."""
        items: list[list[BlockUnion]] = []
        for li in tag.find_all("li", recursive=False):
            item_blocks: list[BlockUnion] = []

            for child in li.children:
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text:
                        item_blocks.append(ParagraphIR(inlines=[TextIR(text=text)]))
                elif isinstance(child, Tag):
                    # Skip item number tags (e.g. <span class="ltx_tag ltx_tag_item">1.</span>)
                    child_classes = set(css_classes(child))
                    if "ltx_tag" in child_classes:
                        continue
                    if child.name in ("ul", "ol"):
                        # Nested list
                        nested = self._build_list_items(child)
                        if nested:
                            item_blocks.append(ListIR(items=nested))
                    elif child.name in ("section", "article", "div", "span"):
                        # Recurse generically so that block-level siblings (e.g.
                        # nested ar5iv lists inside <div class="ltx_para">) are
                        # preserved instead of flattened to raw inline HTML.
                        blocks, _ = self._children_to_blocks(child.children, "", len(item_blocks))
                        item_blocks.extend(blocks)
                    else:
                        inlines = self._tag_to_inlines(child)
                        if inlines:
                            item_blocks.append(ParagraphIR(inlines=inlines))

            if item_blocks:
                items.append(item_blocks)

        return items

    def _build_ar5iv_list_items(self, tag: Tag) -> list[list[BlockUnion]]:
        """Build list items from ar5iv ``<span class="ltx_enumerate">`` etc.

        ar5iv renders lists as ``<span class="ltx_enumerate">`` containing
        ``<span class="ltx_item">`` children.  Each item has a marker tag
        (``ltx_tag_item``) and one or more paragraph-like content tags.
        """
        items: list[list[BlockUnion]] = []
        for item in tag.find_all("span", class_="ltx_item", recursive=False):
            item_blocks: list[BlockUnion] = []
            for child in item.children:
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text:
                        item_blocks.append(ParagraphIR(inlines=[TextIR(text=text)]))
                elif isinstance(child, Tag):
                    child_classes = set(css_classes(child))
                    # Skip item markers
                    if "ltx_tag" in child_classes or "ltx_tag_item" in child_classes:
                        continue
                    # Nested ar5iv list
                    if child.name == "span" and _is_ar5iv_list(child_classes):
                        nested = self._build_ar5iv_list_items(child)
                        if nested:
                            item_blocks.append(
                                ListIR(
                                    ordered=_is_ar5iv_ordered_list(child_classes),
                                    items=nested,
                                )
                            )
                    elif child.name in ("section", "article", "div", "span"):
                        # Recurse generically but keep inline math intact
                        blocks, _ = self._children_to_blocks(child.children, "", len(item_blocks))
                        item_blocks.extend(blocks)
                    else:
                        result = self._tag_to_blocks(child, "", len(item_blocks))
                        if isinstance(result, list):
                            item_blocks.extend(result)
                        elif result is not None:
                            item_blocks.append(result)
            if item_blocks:
                items.append(item_blocks)
        return items


# ── Constants ──────────────────────────────────────────────────────────

_EQUATION_TABLE_RE = re.compile(r"ltx_equationgroup|ltx_eqn_align|ltx_eqn_table|ltx_equation")
_BIB_REF_RE = re.compile(r"#bib\.bib(\d+)")
_FIGURE_CAPTION_RE = re.compile(r"Figure\s+(\d+)", re.I)
_TABLE_CAPTION_RE = re.compile(r"Table\s+(\d+)", re.I)
_ALGORITHM_CAPTION_RE = re.compile(r"Algorithm\s+(\d+)", re.I)
_ARXIV_FRAGMENT_RE = re.compile(r"#[A-Za-z]")


def _is_ar5iv_paragraph(classes: set[str]) -> bool:
    """Return True for ar5iv inline paragraph wrappers.

    ``ltx_p`` is the actual paragraph element; ``ltx_para`` is a wrapper
    that may contain a paragraph plus block-level siblings (e.g. nested
    lists) and should therefore be treated as a generic container.
    """
    return "ltx_p" in classes


def _is_ar5iv_list(classes: set[str]) -> bool:
    """Return True for ar5iv list wrappers."""
    return "ltx_enumerate" in classes or "ltx_itemize" in classes


def _is_ar5iv_ordered_list(classes: set[str]) -> bool:
    """Return True for ordered ar5iv lists."""
    return "ltx_enumerate" in classes


def _is_equation_table(tag: Tag) -> bool:
    """Return True if *tag* is an equation table wrapper."""
    classes = " ".join(css_classes(tag))
    return bool(_EQUATION_TABLE_RE.search(classes))


def _extract_math_latex(tag: Tag) -> str:
    """Extract LaTeX from a <math> tag, normalizing whitespace."""
    annotation = tag.find("annotation", attrs={"encoding": "application/x-tex"})
    latex = annotation.text.strip() if annotation and annotation.text else tag.get_text(" ", strip=True)
    return _normalize_math_latex(latex)


def _normalize_math_latex(latex: str) -> str:
    r"""Normalize math LaTeX for Markdown display.

    Literal newlines inside math (common inside ``\\mbox{...}``) break
    Markdown math rendering; collapse them to spaces and trim surrounding
    whitespace while preserving ``\\\\`` line-break commands.
    """
    # Replace literal newlines/tabs with spaces, then collapse runs of spaces.
    latex = re.sub(r"[\n\r\t]+", " ", latex)
    latex = re.sub(r" {2,}", " ", latex)
    return latex.strip()


def _is_citation_link(href: str) -> bool:
    if not href:
        return False
    return bool(_BIB_REF_RE.search(href))


def _extract_citation_ref(href: str) -> str | None:
    m = _BIB_REF_RE.search(href)
    if m:
        return f"ref-{m.group(1)}"
    return None


def _map_fragment_to_anchor(href: str) -> str | None:
    fragment = href.lstrip("#")
    return _map_arxiv_fragment_to_anchor(fragment)


def _map_arxiv_fragment_to_anchor(fragment: str) -> str | None:
    # Figure: S1.F1 -> figure-1
    m = re.match(r"S\d+\.F(\d+)$", fragment)
    if m:
        return f"figure-{m.group(1)}"
    # Table: S5.T1 -> table-1
    m = re.match(r"[SA]\d*\.?T(\d+)$", fragment)
    if m:
        return f"table-{m.group(1)}"
    # Section: S1 -> section-1
    m = re.match(r"S(\d+)$", fragment)
    if m:
        return f"section-{m.group(1)}"
    # Subsection: S4.SS1 -> section-4-1
    m = re.match(r"S(\d+)\.SS(\d+)$", fragment)
    if m:
        return f"section-{m.group(1)}-{m.group(2)}"
    # Algorithm: alg1 -> algorithm-1
    m = re.match(r"alg(\d+)$", fragment)
    if m:
        return f"algorithm-{m.group(1)}"
    return None


def _extract_figure_id(caption: str) -> str | None:
    m = _FIGURE_CAPTION_RE.search(caption)
    if m:
        return f"figure-{m.group(1)}"
    return None


def _extract_table_id(caption: str) -> str | None:
    m = _TABLE_CAPTION_RE.search(caption)
    if m:
        return f"table-{m.group(1)}"
    return None


def _extract_equation_number(tag: Tag) -> str | None:
    """Extract an equation number from an ar5iv equation table wrapper."""
    eqno_tag = tag.find("span", class_=re.compile(r"ltx_tag_equation"))
    if isinstance(eqno_tag, Tag):
        return _get_equation_number_text(eqno_tag)
    # Older/classic HTML tables place the number in a td with class ltx_eqn_eqno
    eqno_td = tag.find("td", class_=re.compile(r"ltx_eqn_eqno"))
    if isinstance(eqno_td, Tag):
        return _get_equation_number_text(eqno_td)
    return None


def _get_equation_number_text(tag: Tag) -> str | None:
    """Return stripped equation number text, e.g. ``(14)``."""
    text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
    # Remove surrounding brackets if any
    return text or None


def _extract_algorithm_number(caption: str) -> str | None:
    m = _ALGORITHM_CAPTION_RE.search(caption)
    if m:
        return m.group(1)
    return None


def _extract_table_data(
    table: Tag,
    tag_to_inlines: Callable[[Tag], list[InlineUnion]],
) -> tuple[list[list[InlineUnion]], list[list[list[InlineUnion]]]]:
    """Extract headers and rows from a <table> tag.

    Returns:
        headers: One cell per header column, each cell = list[InlineUnion].
        rows: Each row = list of cells, each cell = list[InlineUnion].
    """
    headers: list[list[InlineUnion]] = []
    rows: list[list[list[InlineUnion]]] = []
    all_data_rows: list[list[list[InlineUnion]]] = []

    # Look for thead/tbody/tfoot
    for section in table.find_all(["thead", "tbody", "tfoot"], recursive=False):
        for row in section.find_all("tr", recursive=False):
            cells: list[list[InlineUnion]] = []
            for cell in row.find_all(["th", "td"], recursive=False):
                cells.append(tag_to_inlines(cell))
            if cells:
                if section.name == "thead":
                    if not headers:
                        headers = cells
                else:
                    all_data_rows.append(cells)

    # Fallback: no thead/tbody — use first row as header
    if not headers and not all_data_rows:
        all_rows = table.find_all("tr", recursive=False)
        if all_rows:
            for cell in all_rows[0].find_all(["th", "td"], recursive=False):
                headers.append(tag_to_inlines(cell))
            for row in all_rows[1:]:
                cells = []
                for cell in row.find_all(["th", "td"], recursive=False):
                    cells.append(tag_to_inlines(cell))
                if cells:
                    all_data_rows.append(cells)

    rows = all_data_rows
    return headers, rows


def _is_ltx_listing_container(tag: Tag) -> bool:
    """Outer ``div.ltx_listing`` (not ``ltx_listingline`` rows)."""
    if tag.name != "div":
        return False
    cls = css_classes(tag)
    return "ltx_listing" in cls and "ltx_listingline" not in cls


def _decode_data_plain_href(href: str | None) -> str | None:
    """Decode ``data:text/plain;...;base64,...`` used by ar5iv for embedded listings."""
    if not href or not href.startswith("data:"):
        return None
    if ";base64," not in href:
        return None
    _, b64 = href.split(";base64,", 1)
    try:
        return base64.b64decode(b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _extract_listing_language(cls: str) -> str:
    """Extract the declared language from a ``div.ltx_listing`` class string."""
    m = re.search(r"ltx_lst_language_(\w+)", cls)
    return m.group(1).lower() if m else "text"


_SHELL_COMMANDS: frozenset[str] = frozenset(
    {
        "pip",
        "conda",
        "apt",
        "apt-get",
        "yum",
        "brew",
        "npm",
        "yarn",
        "cargo",
        "curl",
        "wget",
        "git",
        "bash",
        "sh",
        "zsh",
        "make",
        "cmake",
        "gcc",
        "g++",
    }
)


def _normalize_listing_language(declared: str, text: str) -> str:
    """Sanitize the declared listing language against the actual content.

    ar5iv sometimes labels shell commands (e.g. ``pip install ...``) as
    ``ltx_lst_language_Python``.  When the declared language is ``python``
    but the source is not valid Python syntax, fall back to ``bash`` for
    obvious shell commands or ``text`` otherwise.
    """
    declared = declared.lower().strip() if declared else "text"
    if declared == "python":
        try:
            ast.parse(text)
        except SyntaxError:
            first_word = text.lstrip().split(None, 1)[0].lower() if text.strip() else ""
            if first_word in _SHELL_COMMANDS:
                return "bash"
            return "text"
    return declared
