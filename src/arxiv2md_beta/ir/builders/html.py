"""HTML builder: convert arXiv HTML to :class:`DocumentIR`.

Reuses the existing :mod:`arxiv2md_beta.html.parser` for metadata and section
extraction, and converts HTML elements to IR nodes via BeautifulSoup traversal.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from bs4 import BeautifulSoup, NavigableString, Tag

from arxiv2md_beta.ir.assets import SvgAsset
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
from arxiv2md_beta.ir.builders._algorithm import (
    _EQUATION_TABLE_RE,
    _PICTURE_BARE_RE,
    _PICTURE_HBOX_RE,
    _algorithm_keyword,
    _clean_image_alt,
    _decode_data_plain_href,
    _extract_algorithm_number,
    _extract_citation_ref,
    _extract_equation_number,
    _extract_figure_id,
    _extract_listing_language,
    _extract_math_latex,
    _extract_table_data,
    _extract_table_id,
    _get_marker_text,
    _graphic_src,
    _is_ar5iv_list,
    _is_ar5iv_ordered_list,
    _is_ar5iv_paragraph,
    _is_citation_link,
    _is_equation_table,
    _is_ltx_listing_container,
    _nest_algorithm_steps,
    _normalize_listing_language,
    _normalize_math_latex,
    _split_at_breaks,
    _substitute_picture_boxes,
    _svg_foreignobject_latex,
    _trim_inline_edges,
)
from arxiv2md_beta.ir.builders._math_norm import (
    TAG_RE,
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
        images_subdir: str = "images",
    ):
        self.image_map = dict(image_map or {})
        self.image_stem_map = dict(image_stem_map or {})
        self._image_resolver = image_resolver or ImageResolver(
            index_map=self.image_map,
            stem_map=self.image_stem_map,
        )
        self._figure_counter = 0
        self._pending_footnotes: deque[BlockUnion] = deque()
        self._images_subdir = images_subdir
        self._svg_counter = 0
        # Inline <svg> figures collected during build; persisted by the
        # ingestion layer (builder performs no file I/O).
        self._svg_assets: list[SvgAsset] = []
        # _get_text re-serializes + re-parses a tag (expensive); cache per
        # fragment — tags are not mutated during a fragment build. Keyed by
        # the Tag object itself (identity hash), which also pins the tag so
        # its id() cannot be recycled mid-fragment.
        self._text_cache: dict[Tag, str] = {}

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
        from arxiv2md_beta.html.parser import ParsedArxivHtml

        assert isinstance(parsed, ParsedArxivHtml)

        authors = [AuthorIR(name=a.name, affiliations=a.affiliations) for a in parsed.authors]

        # Convert abstract HTML fragment to IR blocks
        abstract_blocks = self._html_to_blocks(parsed.abstract_html)
        # Degraded LaTeXML output may render the whole abstract as an SVG
        # picture (no convertible blocks); fall back to the plain text.
        if not abstract_blocks and parsed.abstract:
            abstract_blocks = [
                ParagraphIR(
                    inlines=[TextIR(text=parsed.abstract)],
                )
            ]

        # Convert front matter HTML fragment to IR blocks
        front_matter_blocks = self._html_to_blocks(parsed.front_matter_html)

        # Convert section tree and drop leaf sections that have no content
        sections = [self._build_section(section_node) for section_node in parsed.sections]
        sections = self._filter_empty_sections(sections)

        doc = DocumentIR(
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
        doc.assets.extend(self._svg_assets)
        return doc

    def _build_from_html(self, html: str, arxiv_id: str) -> DocumentIR:
        from arxiv2md_beta.html.parser import parse_arxiv_html

        parsed = parse_arxiv_html(html)
        return self._build_from_parsed(parsed, arxiv_id)

    # ── Section building ────────────────────────────────────────────────

    def _build_section(self, section_node: Any) -> SectionIR:
        """Convert a ``SectionNode`` into a :class:`SectionIR`."""
        # section_node is from html.parser._extract_sections
        blocks = self._html_to_blocks(section_node.html)
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

    def _html_to_blocks(self, html_fragment: str | None) -> list[BlockUnion]:
        """Convert an HTML fragment string to a list of block IR nodes."""
        if not html_fragment:
            return []
        soup = BeautifulSoup(html_fragment, "html.parser")
        self._text_cache.clear()
        blocks, _ = self._children_to_blocks(soup.children, 0)
        # Flush remaining footnotes at end of fragment
        while self._pending_footnotes:
            blocks.append(self._pending_footnotes.popleft())
        return blocks

    def _children_to_blocks(self, children: Iterable[Any], start_idx: int) -> tuple[list[BlockUnion], int]:
        """Process an iterable of BeautifulSoup nodes into IR blocks.

        Returns the list of blocks and the next available index.  Pending
        footnotes are inserted after each block that generates them, but
        remaining footnotes are *not* flushed — the caller must do that.
        """
        blocks: list[BlockUnion] = []
        idx = start_idx
        for child in children:
            if isinstance(child, NavigableString):
                text = re.sub(r"\s+", " ", str(child)).strip()
                if text:
                    blocks.append(
                        ParagraphIR(
                            inlines=[TextIR(text=text)],
                        )
                    )
                    idx += 1
                continue
            if not isinstance(child, Tag):
                continue
            result = self._tag_to_blocks(child, idx)
            if isinstance(result, list):
                blocks.extend(result)
                idx += len(result)
            elif result is not None:
                blocks.append(result)
                idx += 1
            # Insert any pending footnotes after the current block
            while self._pending_footnotes:
                blocks.append(self._pending_footnotes.popleft())
        return blocks, idx

    def _tag_to_blocks(self, tag: Tag, base_idx: int) -> list[BlockUnion] | BlockUnion | None:
        """Convert a BeautifulSoup tag to one or more block IR nodes."""
        tag_name = tag.name

        # arXiv / ar5iv code listings (must come before generic div recursion)
        if tag_name == "div" and _is_ltx_listing_container(tag):
            code = self._build_listing(tag)
            return code if code is not None else []

        classes = set(css_classes(tag))

        # ar5iv bibliography "Cited by: §N, §M ..." cross-reference block.
        # It lists where in this paper the reference is cited — layout noise
        # for a standalone Markdown reference list; drop it.
        if "ltx_bib_cited" in classes:
            return None

        # ar5iv paragraph wrappers (span.ltx_p / span.ltx_para) should be treated
        # as paragraphs so that inline math inside them is not emitted as raw HTML.
        if tag_name == "p" or (tag_name == "span" and _is_ar5iv_paragraph(classes)):
            inlines = self._tag_to_inlines(tag)
            if not inlines:
                return None
            # If the paragraph contains display math, lift those equations out as
            # block-level elements so the Markdown emitter can render them with
            # proper $$ delimiters instead of inline math breaking list layout.
            split_blocks = self._split_paragraph_inlines(inlines)
            if len(split_blocks) == 1:
                return split_blocks[0]
            return split_blocks

        # ar5iv lists (span.ltx_enumerate / span.ltx_itemize)
        if tag_name in ("ul", "ol") or (tag_name == "span" and _is_ar5iv_list(classes)):
            items = self._build_ar5iv_list_items(tag) if tag_name == "span" else self._build_list_items(tag)
            if not items:
                return None
            start: int | None = None
            if tag_name == "ol":
                raw_start = attr_optional(tag, "start")
                try:
                    parsed_start = int(str(raw_start)) if raw_start is not None else 1
                except ValueError:
                    parsed_start = 1
                if parsed_start != 1:
                    start = parsed_start
            return ListIR(
                ordered=(tag_name == "ol" or _is_ar5iv_ordered_list(classes)),
                start=start,
                items=items,
            )

        # ar5iv equation tables rendered as <span class="ltx_equation ltx_eqn_table">
        if tag_name in ("span", "div") and _is_equation_table(tag):
            latex = self._extract_equation_latex(tag)
            if not latex:
                return None
            return EquationIR(
                latex=latex,
                equation_number=_extract_equation_number(tag),
            )

        # Container elements — recurse directly without re-parsing
        if tag_name in ("section", "article", "div", "span"):
            blocks, _ = self._children_to_blocks(tag.children, base_idx)
            return blocks

        # Headings
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_name[1])
            anchor = attr_optional(tag, "id") or ""
            # Use inline conversion (not _get_text) so <math> renders as a
            # MathIR; get_text would concatenate the MathML unicode render AND
            # the application/x-tex annotation (e.g. "𝒙 \bm{x}").
            inlines = self._tag_to_inlines(tag)
            if not inlines:
                return None
            return HeadingIR(
                level=level,
                anchor=anchor,
                inlines=inlines,
            )

        # Figures
        if tag_name == "figure":
            return self._build_figure(tag)

        # Tables
        if tag_name == "table":
            return self._build_table(tag)

        # Blockquote
        if tag_name == "blockquote":
            inner_blocks, _ = self._children_to_blocks(tag.children, base_idx)
            if not inner_blocks:
                return None
            return BlockQuoteIR(
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
                language=lang or None,
                text=text,
            )

        if tag_name == "code":
            return CodeIR(
                text=tag.get_text(),
            )

        # Horizontal rule
        if tag_name == "hr":
            return RuleIR()

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
                    latex=latex,
                )
            return ParagraphIR(
                inlines=[MathIR(latex=latex, display=False)],
            )

        # Raw fallback
        return RawBlockIR(
            format="html",
            content=str(tag),
        )

    # ── Inline conversion ──────────────────────────────────────────────

    def _split_paragraph_inlines(
        self,
        inlines: list[InlineUnion],
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
                        inlines=list(current),
                    )
                )
            current = []

        for il in inlines:
            if il.type == "math" and getattr(il, "display", False):
                _flush_current()
                blocks.append(
                    EquationIR(
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
                # Collapse internal whitespace (HTML source line wraps) so a
                # soft-wrapped sentence like "counting\nthe objects" does not
                # emit as two lines. Leading/trailing space is preserved to
                # keep word separation between adjacent inline siblings.
                text = re.sub(r"\s+", " ", str(child))
                if not text.strip():
                    # A standalone space node between inline siblings is real
                    # word separation: LaTeXML writes "<span>Figure</span> "
                    # "<span>2</span>", and dropping the node glues "Figure2".
                    # Emit a single space; block-level runs of whitespace-only
                    # text are still dropped by _flush_current, so no blank
                    # lines can appear in tables/lists.
                    text = " "
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

            # Internal link — the raw fragment stays in target_id verbatim.
            # Real anchors only exist after NumberingPass, whose label→anchor
            # map (keyed by the ar5iv element id kept as block label) repoints
            # these links post-transform. Guessing a global counter here
            # pointed §2+ references at the wrong figure/table (audit5 C1:
            # LaTeXML ids are section-local, caption numbers are global).
            if href.startswith("#"):
                return LinkIR(
                    kind="internal",
                    target_id=href[1:],
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

        # Skip inline SVG elements — these are typographic/decorative renderings
        # (e.g. LaTeXML's colored-highlight boxes around math) and would leak
        # raw <svg> markup into the Markdown if emitted as fallback HTML.
        if tag_name == "svg":
            return None

        # Images — <object data="..."> is LaTeXML's vector-figure form of <img>
        if tag_name in ("img", "object"):
            src = self._image_resolver.resolve(_graphic_src(tag))
            alt = _clean_image_alt(attr_str(tag, "alt"))
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

            # Float/line tags ("Figure 1", "Algorithm 1", equation numbers) are
            # structural labels. Their inner spans carry bold styling that would
            # become Markdown emphasis and nest with the caption-level bold the
            # emitters add (``****Algorithm 1** ...**``). Recurse on a copy with
            # font styling stripped, so the label keeps its exact internal
            # spacing ("Table 2: ") as plain text.
            if "ltx_tag" in classes:
                tag_copy = BeautifulSoup(str(tag), "html.parser").find(class_=re.compile(r"ltx_tag"))
                if isinstance(tag_copy, Tag):
                    for el in tag_copy.find_all(True):
                        cls = el.get("class")
                        if cls:
                            # bs4's stubs type el["class"] as str | AttributeValueList;
                            # a plain list assignment is accepted at runtime.
                            el["class"] = cast("Any", [c for c in cls if not c.startswith("ltx_font_")])
                    result = self._tag_to_inlines(tag_copy)
                else:
                    result = self._tag_to_inlines(tag)
                # Drop the tag's own edge whitespace nodes ("<span>Algorithm 1</span> ");
                # content-bearing edges like "Table 2: " keep their boundary space.
                while result and isinstance(result[-1], TextIR) and not result[-1].text.strip():
                    result = result[:-1]
                while result and isinstance(result[0], TextIR) and not result[0].text.strip():
                    result = result[1:]
                return result

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
        r"""Get normalized text content from a tag.

        ``<annotation encoding="application/x-tex">`` nodes duplicate the
        MathML rendering (the unicode glyph) with the raw LaTeX source; drop
        them so ``get_text`` yields e.g. ``𝒙`` instead of ``𝒙 \bm{x}``.
        """
        cached = self._text_cache.get(tag)
        if cached is not None:
            return cached
        source = re.sub(
            r"<annotation[^>]*>.*?</annotation>",
            "",
            str(tag),
            flags=re.DOTALL,
        )
        text = BeautifulSoup(source, "html.parser").get_text(" ", strip=True)
        result = re.sub(r"\s+", " ", text).strip()
        self._text_cache[tag] = result
        return result

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
        # Recover TikZ boxed symbols from the sibling <svg> foreignobjects.
        if _PICTURE_HBOX_RE.search(latex):
            symbols = [_svg_foreignobject_latex(s) for s in tag.find_all("svg")]
            latex = _substitute_picture_boxes(latex, symbols)
        latex = _normalize_math_latex(latex)
        # Drop any surviving picture fragment (no recoverable symbol).
        latex = _PICTURE_HBOX_RE.sub(" ", latex)
        latex = _PICTURE_BARE_RE.sub(" ", latex)
        # Strip outer display-math delimiters if present
        if latex.startswith("$$") and latex.endswith("$$"):
            latex = latex[2:-2]
        elif latex.startswith("$") and latex.endswith("$"):
            latex = latex[1:-1]

        # Strip any existing \tag{...} from the LaTeX annotation; the
        # authoritative paper number lives in the HTML table cell and is
        # extracted separately via _extract_equation_number().
        latex = TAG_RE.sub("", latex).strip()
        return latex

    # ── Complex block builders ─────────────────────────────────────────

    def _build_figure(self, tag: Tag) -> BlockUnion | None:
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

        # Caption-extracted id only (e.g. "figure-3"). Uncaptioned figures get
        # no id here — NumberingPass is the single numbering source and assigns
        # one, avoiding builder/pass counter drift.
        fig_id = _extract_figure_id(caption_text)
        # Element id (e.g. "S1.F1") is kept as label; NumberingPass turns
        # labels into a fragment→anchor map so internal links resolve to the
        # final anchor instead of a guessed global counter.
        tag_id = attr_optional(tag, "id")

        # Algorithm figure
        if "ltx_float_algorithm" in tag_classes or "ltx_algorithm" in tag_classes:
            alg_num = _extract_algorithm_number(caption_text)
            return AlgorithmIR(
                anchor=fig_id,
                label=tag_id,
                caption=caption,
                algorithm_number=alg_num,
                steps=self._algorithm_steps(
                    tag,
                    caption_tag if isinstance(caption_tag, Tag) else None,
                ),
            )

        # Table figure
        if "ltx_table" in tag_classes:
            inner_table = tag.find("table")
            if isinstance(inner_table, Tag):
                return self._build_table(
                    inner_table,
                    figure_caption=caption,
                    figure_caption_text=caption_text,
                    figure_label=tag_id,
                )

        # Image figure (default) — resolve local image paths
        # Inline SVG: no file I/O here. Every svg goes through the single
        # registration entry — the raw markup rides in a SvgAsset and the
        # ingestion layer persists it to <images_subdir>/figure-N.svg after
        # the build. A figure mixing <img> and <svg> used to skip this
        # entirely (the fallback ran only with no <img>), dropping the svg
        # and leaving the shared svg_src below pointing at a stale counter
        # value (audit5 G2-4). Both kinds share one document-order strip.
        # <object data="...">: LaTeXML emits vector figures this way
        # (\includegraphics of a PDF converted to an external SVG) instead
        # of <img>; skipping it dropped the whole figure, caption included.
        imgs: list[Tag] = [t for t in tag.find_all(["img", "svg", "object"]) if isinstance(t, Tag)]
        # Drop <img> fallbacks nested inside an <object> so the same graphic
        # is not collected twice.
        imgs = [t for t in imgs if t.name != "img" or t.find_parent("object") is None]
        svg_srcs: dict[int, str] = {id(t): self._register_svg_asset(t) for t in imgs if t.name == "svg"}
        # ar5iv sometimes renders a table as a vector <svg> inside
        # <figure class="ltx_table"> with no <table> and no <img>. With nothing
        # to show, emitting only the caption produces a misleading orphan
        # "> Table N: ..." block; skip it (the rendered table lives in the PDF).
        if not imgs:
            return None

        # Preserve table-layout figures: ar5iv encodes multi-row panel grids
        # (e.g. a 4x4 toy-experiment figure) as an inner <table> with one
        # <td> per panel. Without this the rows collapse into a flat image
        # strip and the grid is lost.
        grid: list[list[list[InlineUnion]]] | None = None
        inner_table = tag.find("table")
        if isinstance(inner_table, Tag) and any(tr.find(["img", "object"]) for tr in inner_table.find_all("tr")):
            rows: list[list[list[InlineUnion]]] = []
            for tr in inner_table.find_all("tr"):
                cells = tr.find_all(["td", "th"], recursive=False)
                if not cells:
                    continue
                rows.append([self._tag_to_inlines(cell) for cell in cells])
            if rows:
                width = max(len(r) for r in rows)
                for r in rows:
                    while len(r) < width:
                        r.append([])  # pad short rows so the grid stays rectangular
                grid = rows

        figure_index = self._figure_counter + 1  # 1-based for image_map lookup
        images = [
            ImageRefIR(
                src=(svg_srcs[id(img)] if img.name == "svg" else self._resolve_image_src(img, figure_index)),
                alt=_clean_image_alt(attr_str(img, "alt")),
            )
            for img in imgs
        ]

        # Grid cells were built through the generic inline path, which resolves
        # by stem only. Re-map their srcs to the flat-list resolution above,
        # which carries figure_index (handles ar5iv's opaque ``xN.png`` names
        # that have no stem match).
        if grid:
            resolved_by_src: dict[str, str] = {}
            for img_tag, img_ref in zip(imgs, images, strict=False):
                resolved_by_src.setdefault(_graphic_src(img_tag), img_ref.src)
            if resolved_by_src:
                for row in grid:
                    for cell in row:
                        for inline in cell:
                            if isinstance(inline, ImageRefIR) and inline.src in resolved_by_src:
                                inline.src = resolved_by_src[inline.src]

        self._figure_counter += 1
        return FigureIR(
            figure_id=fig_id,
            anchor=fig_id,
            label=tag_id,
            images=images,
            caption=caption,
            kind="image",
            grid=grid,
        )

    def _resolve_image_src(self, img_tag: Tag, figure_index: int) -> str:
        """Resolve an <img>/<object> graphic src to a local path via :class:`ImageResolver`."""
        src = _graphic_src(img_tag)
        if not src:
            return src
        return self._image_resolver.resolve(src, figure_index=figure_index)

    def _build_table(
        self,
        tag: Tag,
        *,
        figure_caption: list | None = None,
        figure_caption_text: str = "",
        figure_label: str | None = None,
    ) -> BlockUnion | None:
        """Build a TableIR or EquationIR from a <table> tag.

        When the table sits inside a ``<figure class="ltx_table">`` (the usual
        ar5iv shape), the figure-level ``<figcaption>`` is passed in via
        ``figure_caption``/``figure_caption_text`` — ar5iv puts the caption
        there, not inside ``<table><caption>``.
        """
        classes = " ".join(css_classes(tag))

        # Equation tables
        if _EQUATION_TABLE_RE.search(classes):
            # Prefer LaTeX from <math> annotations; fall back to plain text
            latex = self._extract_equation_latex(tag)
            if not latex:
                return None
            return EquationIR(
                latex=latex,
                equation_number=_extract_equation_number(tag),
            )

        # Data tables
        headers, rows = _extract_table_data(tag, self._tag_to_inlines)
        if not rows and not headers:
            return None

        # Caption: <table><caption> wins; figcaption of the wrapping figure
        # is the fallback (and, for ar5iv output, the usual source).
        caption: list = []
        caption_text = ""
        caption_tag = tag.find("caption")
        if isinstance(caption_tag, Tag):
            caption = self._tag_to_inlines(caption_tag)
            caption_text = self._get_text(caption_tag)
        elif figure_caption or figure_caption_text:
            caption = figure_caption or []
            caption_text = figure_caption_text
        table_id = _extract_table_id(caption_text)

        return TableIR(
            table_id=table_id,
            headers=headers,
            rows=rows,
            caption=caption,
            label=figure_label,
        )

    def _register_svg_asset(self, svg: Tag) -> str:
        """Register an inline <svg> for post-build persistence; return its src.

        Single entry for every svg→image conversion (audit5 G2-4): bumps the
        svg counter, carries the raw markup in a SvgAsset, and returns the
        ``<images_subdir>/figure-N.svg`` path the ingestion layer will write.
        """
        self._svg_counter += 1
        svg_src = f"{self._images_subdir}/figure-{self._svg_counter}.svg"
        self._svg_assets.append(SvgAsset(path=svg_src, content=str(svg)))
        return svg_src

    def _build_listing(self, tag: Tag) -> CodeIR | None:
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
                language=lang,
                text=body,
            )
        return None

    def _algorithm_steps(self, tag: Tag, caption_tag: Tag | None) -> list[BlockUnion]:
        """Collect the pseudocode body of an algorithm float (audit5 C2).

        ar5iv wraps algorithm bodies either in ``div.ltx_listing`` containers
        (rebuilt line-by-line with real math IR by
        :meth:`_build_algorithm_listing`) or, more rarely, in plain ``ltx_p``
        paragraphs. The caption tag is excluded from the fallback scan. Without
        this the ``AlgorithmIR.steps`` list stayed empty for every algorithm
        and the pseudocode never reached the output — only the caption line
        was emitted.
        """
        steps: list[BlockUnion] = []
        for listing in tag.find_all("div", class_=re.compile(r"ltx_listing")):
            if not _is_ltx_listing_container(listing):
                continue  # an inner listingline row, not a listing container
            steps.extend(self._build_algorithm_listing(listing))
        if steps:
            return _nest_algorithm_steps(steps)
        body, _ = self._children_to_blocks(
            [c for c in tag.children if c is not caption_tag],
            0,
        )
        return body

    def _build_algorithm_listing(self, listing: Tag) -> list[BlockUnion]:
        r"""Rebuild algorithm pseudocode rows as paragraph steps with real math.

        Raw ``get_text`` reconstruction (the ``_build_listing`` path) is wrong
        for pseudocode: inline math degrades to unicode+LaTeX soup
        (``pS(⋅∣x)p_{S}(\cdot\mid x)``), and LaTeXML sometimes collapses the
        whole body into a single ``ltx_listingline`` with the algorithmic
        keywords left as ``<span class="ltx_ERROR undefined">\State</span>``
        markers (e.g. arXiv 2601.18734) — concatenating ``\StateLet``.
        Instead each row becomes one paragraph via the generic inline path,
        and unknown-command markers are turned into line breaks (with a
        readable keyword where one exists).
        """
        rows = [
            r
            for r in listing.find_all("div", class_=re.compile(r"ltx_listingline"), recursive=False)
            if isinstance(r, Tag)
        ]
        if not rows:
            return []
        steps: list[BlockUnion] = []
        for row in rows:
            # Work on a copy so the shared soup (used by other passes) stays intact.
            row_soup = BeautifulSoup(str(row), "html.parser")
            row_copy = row_soup.find("div", class_="ltx_listingline")
            if not isinstance(row_copy, Tag):
                continue
            line_num = row_copy.find("span", class_=re.compile(r"ltx_tag_listingline"))
            if isinstance(line_num, Tag):
                line_num.decompose()
            for marker in row_copy.find_all(class_="ltx_ERROR"):
                keyword = _algorithm_keyword(_get_marker_text(marker))
                br = row_soup.new_tag("br")
                marker.replace_with(br)
                if keyword:
                    br.insert_after(NavigableString(keyword + " "))
            inlines = self._tag_to_inlines(row_copy)
            for segment in _split_at_breaks(inlines):
                segment = _trim_inline_edges(segment)
                if segment:
                    steps.append(ParagraphIR(inlines=segment))
        return steps

    def _build_list_items(self, tag: Tag) -> list[list[BlockUnion]]:
        """Build list items from a <ul> or <ol> tag."""
        items: list[list[BlockUnion]] = []
        for li in tag.find_all("li", recursive=False):
            item_blocks: list[BlockUnion] = []

            for child in li.children:
                if isinstance(child, NavigableString):
                    text = re.sub(r"\s+", " ", str(child)).strip()
                    if text:
                        item_blocks.append(ParagraphIR(inlines=[TextIR(text=text)]))
                elif isinstance(child, Tag):
                    # Skip item number tags (e.g. <span class="ltx_tag ltx_tag_item">1.</span>)
                    child_classes = set(css_classes(child))
                    if "ltx_tag" in child_classes:
                        continue
                    # ar5iv bibliography "Cited by:" cross-reference — noise.
                    if "ltx_bib_cited" in child_classes:
                        continue
                    if child.name in ("ul", "ol"):
                        # Nested list — keep the ordered flag (an <ol> used to
                        # degrade to bullets here) and its start number.
                        nested = self._build_list_items(child)
                        if nested:
                            start: int | None = None
                            if child.name == "ol":
                                raw_start = attr_optional(child, "start")
                                try:
                                    parsed_start = int(str(raw_start)) if raw_start is not None else 1
                                except ValueError:
                                    parsed_start = 1
                                if parsed_start != 1:
                                    start = parsed_start
                            item_blocks.append(ListIR(items=nested, ordered=(child.name == "ol"), start=start))
                    elif child.name in ("section", "article", "div", "span"):
                        # Recurse generically so that block-level siblings (e.g.
                        # nested ar5iv lists inside <div class="ltx_para">) are
                        # preserved instead of flattened to raw inline HTML.
                        blocks, _ = self._children_to_blocks(child.children, len(item_blocks))
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
                    text = re.sub(r"\s+", " ", str(child)).strip()
                    if text:
                        item_blocks.append(ParagraphIR(inlines=[TextIR(text=text)]))
                elif isinstance(child, Tag):
                    child_classes = set(css_classes(child))
                    # Skip item markers
                    if "ltx_tag" in child_classes or "ltx_tag_item" in child_classes:
                        continue
                    # ar5iv bibliography "Cited by:" cross-reference — noise.
                    if "ltx_bib_cited" in child_classes:
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
                        blocks, _ = self._children_to_blocks(child.children, len(item_blocks))
                        item_blocks.extend(blocks)
                    else:
                        result = self._tag_to_blocks(child, len(item_blocks))
                        if isinstance(result, list):
                            item_blocks.extend(result)
                        elif result is not None:
                            item_blocks.append(result)
            if item_blocks:
                items.append(item_blocks)
        return items


# ── Constants ──────────────────────────────────────────────────────────
