"""HTML builder: convert arXiv HTML to :class:`DocumentIR`.

Reuses the existing :mod:`arxiv2md_beta.html.parser` for metadata and section
extraction, and converts HTML elements to IR nodes via BeautifulSoup traversal.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag  # type: ignore[import-untyped]

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
        image_map: dict[int, str] | None = None,
        image_stem_map: dict[str, str] | None = None,
        image_resolver: ImageResolver | None = None,
    ):
        self.image_map = image_map or {}
        self.image_stem_map = image_stem_map or {}
        self._image_resolver = image_resolver or ImageResolver(
            index_map=self.image_map,
            stem_map=self.image_stem_map,
        )
        self._figure_counter = 0
        self._used_image_indices: set[int] = set()
        self._pending_footnotes: deque[BlockUnion] = deque()

    # ── Public API ─────────────────────────────────────────────────────

    def build(self, source: Any, **kwargs: Any) -> DocumentIR:
        """Parse HTML *source* (str or bytes) into a :class:`DocumentIR`."""
        if isinstance(source, bytes):
            source = source.decode("utf-8", errors="replace")
        arxiv_id = kwargs.get("arxiv_id", "unknown")
        return self._build_from_html(source, arxiv_id)

    def _build_from_html(self, html: str, arxiv_id: str) -> DocumentIR:
        soup = BeautifulSoup(html, "html.parser")

        # Reuse existing parser for metadata and section structure
        from arxiv2md_beta.html.parser import (
            _extract_title,
            _extract_authors_with_affiliations,
            _extract_abstract,
            _extract_abstract_html,
            _extract_front_matter_html,
            _extract_sections,
            _extract_submission_date,
            _find_document_root,
        )

        document_root = _find_document_root(soup)
        title = _extract_title(soup)
        parsed_authors = _extract_authors_with_affiliations(soup)
        authors = [AuthorIR(name=a.name, affiliations=a.affiliations) for a in parsed_authors]
        abstract_text = _extract_abstract(soup)
        abstract_html = _extract_abstract_html(soup)
        front_matter_html = _extract_front_matter_html(soup, document_root)
        submission_date = _extract_submission_date(soup)

        # Convert abstract HTML fragment to IR blocks
        abstract_blocks = self._html_to_blocks(abstract_html, section_id="abstract")

        # Convert front matter HTML fragment to IR blocks
        front_matter_blocks = self._html_to_blocks(front_matter_html, section_id="front_matter")

        # Convert section tree
        section_nodes = _extract_sections(document_root)
        sections = [self._build_section(sn) for sn in section_nodes]

        return DocumentIR(
            metadata=PaperMetadata(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                submission_date=submission_date,
                abstract_text=abstract_text,
                parser="html",
            ),
            abstract=abstract_blocks,
            front_matter=front_matter_blocks,
            sections=sections,
        )

    # ── Section building ────────────────────────────────────────────────

    def _build_section(self, section_node) -> SectionIR:
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
        self, children, section_id: str, start_idx: int
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
                    blocks.append(ParagraphIR(
                        section_id=section_id,
                        order_index=idx,
                        inlines=[TextIR(text=text)],
                    ))
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

    def _tag_to_blocks(
        self, tag: Tag, section_id: str, base_idx: int
    ) -> list[BlockUnion] | BlockUnion | None:
        """Convert a BeautifulSoup tag to one or more block IR nodes."""
        tag_name = tag.name

        # Container elements — recurse directly without re-parsing
        if tag_name in ("section", "article", "div", "span"):
            blocks, _ = self._children_to_blocks(tag.children, section_id, base_idx)
            return blocks

        # Headings
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_name[1])
            text = self._get_text(tag)
            anchor = tag.get("id") or ""
            if not text:
                return None
            return HeadingIR(
                section_id=section_id,
                order_index=base_idx,
                level=level,
                anchor=anchor,
                inlines=[TextIR(text=text)],
            )

        # Paragraph
        if tag_name == "p":
            inlines = self._tag_to_inlines(tag)
            if not inlines:
                return None
            return ParagraphIR(
                section_id=section_id,
                order_index=base_idx,
                inlines=inlines,
            )

        # Lists
        if tag_name in ("ul", "ol"):
            items = self._build_list_items(tag)
            if not items:
                return None
            return ListIR(
                section_id=section_id,
                order_index=base_idx,
                ordered=(tag_name == "ol"),
                items=items,
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
            if code_tag:
                classes = code_tag.get("class", [])
                for cls in classes:
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

        # Raw fallback
        return RawBlockIR(
            section_id=section_id,
            order_index=base_idx,
            format="html",
            content=str(tag),
        )

    # ── Inline conversion ──────────────────────────────────────────────

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
            href = tag.get("href", "")
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

        # Math
        if tag_name == "math":
            annotation = tag.find("annotation", attrs={"encoding": "application/x-tex"})
            if annotation and annotation.text:
                latex = annotation.text.strip()
            else:
                latex = tag.get_text(" ", strip=True)
            return MathIR(latex=latex, display=False)

        # Images
        if tag_name == "img":
            src = tag.get("src", "")
            alt = tag.get("alt", "")
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
            classes = " ".join(tag.get("class", []))

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
        marker_text = self._get_text(mark) if mark else ""

        # Extract content from .ltx_note_content
        content_tag = tag.find("span", class_="ltx_note_content")
        if content_tag:
            # Parse a copy to avoid mutating the original tree
            content_copy = BeautifulSoup(str(content_tag), "html.parser").find(
                "span", class_="ltx_note_content"
            )
            if content_copy:
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
        """Extract LaTeX from an equation table, preferring <math> annotations.

        ar5iv renders equations as HTML text *alongside* <math> tags.
        Using ``get_text()`` would concatenate both the Unicode rendering and
        the LaTeX annotation, producing duplicated garbage.  We collect *only*
        the ``<annotation encoding="application/x-tex">`` nodes inside every
        <math> child, join them, and fall back to plain text only when no math
        annotation is present.
        """
        math_tags = tag.find_all("math")
        latex_parts: list[str] = []
        for math_tag in math_tags:
            annotation = math_tag.find("annotation", attrs={"encoding": "application/x-tex"})
            if annotation and annotation.text:
                latex_parts.append(annotation.text.strip())
        if latex_parts:
            latex = " ".join(latex_parts)
            # Strip outer display-math delimiters if present
            if latex.startswith("$$") and latex.endswith("$$"):
                latex = latex[2:-2]
            elif latex.startswith("$") and latex.endswith("$"):
                latex = latex[1:-1]
            return latex
        # Fallback: plain text without math tags
        text = self._get_text(tag)
        if text.startswith("$") and text.endswith("$"):
            text = text[1:-1]
        return text

    # ── Complex block builders ─────────────────────────────────────────

    def _build_figure(self, tag: Tag, section_id: str, base_idx: int) -> BlockUnion | None:
        """Build a FigureIR or AlgorithmIR from a <figure> tag."""
        classes = " ".join(tag.get("class", []))

        # Caption
        caption_tag = tag.find("figcaption")
        if not caption_tag:
            caption_tag = tag.find("span", class_=re.compile(r"ltx_caption"))
        caption = self._tag_to_inlines(caption_tag) if caption_tag else []
        caption_text = self._get_text(caption_tag) if caption_tag else ""

        # Extract figure number from caption
        fig_id = _extract_figure_id(caption_text) or f"figure-{self._figure_counter}"

        # Algorithm figure
        if "ltx_float_algorithm" in classes or "ltx_algorithm" in classes:
            alg_num = _extract_algorithm_number(caption_text)
            return AlgorithmIR(
                section_id=section_id,
                order_index=base_idx,
                anchor=fig_id,
                caption=caption,
                algorithm_number=alg_num,
            )

        # Table figure
        if "ltx_table" in classes:
            inner_table = tag.find("table")
            if inner_table:
                return self._build_table(inner_table, section_id, base_idx)

        # Image figure (default) — resolve local image paths
        imgs = tag.find_all("img")
        figure_index = self._figure_counter + 1  # 1-based for image_map lookup
        images = [
            ImageRefIR(
                src=self._resolve_image_src(img, figure_index),
                alt=img.get("alt", ""),
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
        src = img_tag.get("src", "")
        if not src:
            return src
        return self._image_resolver.resolve(src, figure_index=figure_index)

    def _build_table(self, tag: Tag, section_id: str, base_idx: int) -> BlockUnion | None:
        """Build a TableIR or EquationIR from a <table> tag."""
        classes = " ".join(tag.get("class", []))

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
            )

        # Data tables
        headers, rows = _extract_table_data(tag, self._tag_to_inlines)
        if not rows and not headers:
            return None

        # Caption
        caption_tag = tag.find("caption")
        caption = self._tag_to_inlines(caption_tag) if caption_tag else []
        caption_text = self._get_text(caption_tag) if caption_tag else ""
        table_id = _extract_table_id(caption_text)

        return TableIR(
            section_id=section_id,
            order_index=base_idx,
            table_id=table_id,
            headers=headers,
            rows=rows,
            caption=caption,
        )

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
                    child_classes = " ".join(child.get("class", []))
                    if "ltx_tag" in child_classes:
                        continue
                    if child.name in ("ul", "ol"):
                        # Nested list
                        nested = self._build_list_items(child)
                        if nested:
                            item_blocks.append(ListIR(items=nested))
                    else:
                        inlines = self._tag_to_inlines(child)
                        if inlines:
                            item_blocks.append(ParagraphIR(inlines=inlines))

            if item_blocks:
                items.append(item_blocks)

        return items


# ── Constants ──────────────────────────────────────────────────────────

_EQUATION_TABLE_RE = re.compile(r"ltx_equationgroup|ltx_eqn_align|ltx_eqn_table")
_BIB_REF_RE = re.compile(r"#bib\.bib(\d+)")
_FIGURE_CAPTION_RE = re.compile(r"Figure\s+(\d+)", re.I)
_TABLE_CAPTION_RE = re.compile(r"Table\s+(\d+)", re.I)
_ALGORITHM_CAPTION_RE = re.compile(r"Algorithm\s+(\d+)", re.I)
_ARXIV_FRAGMENT_RE = re.compile(r"#[A-Za-z]")


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


def _extract_algorithm_number(caption: str) -> str | None:
    m = _ALGORITHM_CAPTION_RE.search(caption)
    if m:
        return m.group(1)
    return None


def _extract_table_data(
    table: Tag,
    tag_to_inlines,
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
