"""Convert arXiv HTML to Markdown with a custom serializer."""

from __future__ import annotations

import base64
import html as html_module
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from arxiv2md_beta.settings import get_settings

try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag
except ImportError as exc:  # pragma: no cover - runtime dependency check
    raise RuntimeError("BeautifulSoup4 is required for HTML parsing (pip install beautifulsoup4).") from exc


_EQUATION_TABLE_RE = re.compile(r"ltx_equationgroup|ltx_eqn_align|ltx_eqn_table")
_MATRIX_RE = re.compile(
    r"matrix\s*\(\s*([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s*\)",
    re.I,
)
_RAISEBOX_RE = re.compile(
    r"\\raisebox\{[^}]+\}\{\\hbox to 0\.0\s*pt\{\\hss\\vbox to 0\.0\s*pt\{\\hbox\{\$([^$]*)\$\}\\vss\}\}\}",
    re.DOTALL,
)
_TRAIL_EQN_RE = re.compile(r"\$\s*\((\d+)\)\s*$")
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")
_LATEX_COMMENT_RE = re.compile(r"(?<!\\)%")
_LATEX_UNDERSCORE_RE = re.compile(r"\\([_^])")
_LATEX_BRACKET_RE = re.compile(r"\\(?=[\[\]])")
_WHITESPACE_RE = re.compile(r"\s+")
_CITE_HREF_RE = re.compile(r'#\b(ref|citation|cite|footnote|fn|endnote|note)[-_]?\d+', re.I)
_FIGURE_FRAG_RE = re.compile(r"S\d+\.F(\d+)$")
_TABLE_FRAG_RE = re.compile(r"[SA]\d*\.?T(\d+)$")
_APPENDIX_FRAG_RE = re.compile(r"A(\d+)$")
_ALG_FRAG_RE = re.compile(r"alg(\d+)$")
_SECTION_FRAG_RE = re.compile(r"S(\d+)$")
_SUBSECTION_FRAG_RE = re.compile(r"S(\d+)\.SS(\d+)$")
_TABLE_PART_RE = re.compile(r"\bltx_t(head|body|foot)\b")
_INLINE_MATH_RE = re.compile(r"^\$([^$]+)\$\s*\((\d+)\)\s*$")
_EQN_TRAIL_NUM_RE = re.compile(r"\s*\((\d+)\)\s*$")
_DISPLAY_MATH_DOLLAR_RE = re.compile(r"^\$([^$]*)\$")
_TABLE_CAPTION_RE = re.compile(r"Table\s+(\d+)\s*[:.]", re.I)
_ALGORITHM_CAPTION_RE = re.compile(r"Algorithm\s+(\d+)\s*[:.\s]", re.I)
_FIGURE_CAPTION_RE = re.compile(r"Figure\s+(\d+)\s*[:.]", re.I)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _simplify_display_math(content: str) -> str:
    """Simplify display math for markdown compatibility: remove $ and complex layout that break parsing.

    ar5iv annotations can contain $ (e.g. \\hbox{$...$}) and \\raisebox/hbox/vbox that cause:
    - Markdown parsers to treat inner $ as inline math delimiters
    - KaTeX/MathJax to fail on complex layout
    We simplify by removing $ and replacing complex layout with semantic content.
    """
    # 1. Simplify \raisebox{\hbox to 0.0pt{\hss\vbox to 0.0pt{\hbox{$X$}\vss}}} -> X
    #    ar5iv converts \ensuremath to \hbox{$...$}; in display math we don't need the $
    #    Allow \s* for line breaks (HTML annotation may have "0.0%\npt")
    content = _RAISEBOX_RE.sub(r"\1", content)
    # 2. Remove trailing $ before equation number: "$ (1)" -> "(1)"
    content = _TRAIL_EQN_RE.sub(r"(\1)", content)
    # 3. Replace $} with } (fix \hbox{...$} without breaking brace structure)
    content = content.replace("$}", "}")
    # 4. Remove all remaining unescaped $ (they break markdown $$ block parsing)
    content = _UNESCAPED_DOLLAR_RE.sub("", content)
    return content


def _sanitize_display_math(content: str) -> str:
    """Legacy: now delegates to _simplify_display_math."""
    return _simplify_display_math(content)


def _svg_replace_foreignobject_with_text(svg_html: str) -> str:
    """Replace <foreignObject> (HTML text) with SVG <text> so text renders when SVG is used as image.

    Browsers do not render HTML inside <foreignObject> when the SVG is loaded as a separate
    file (e.g. via <img> or Markdown ![]()). Converting to <text> makes labels visible.
    """
    soup = BeautifulSoup(svg_html, "html.parser")
    svg_elem = soup.find("svg")
    if not svg_elem:
        return svg_html

    ms = get_settings().markdown_svg
    for fo in list(svg_elem.find_all("foreignobject")):
        text = fo.get_text(separator=" ", strip=True)
        if not text:
            fo.decompose()
            continue
        # Decode HTML entities (e.g. &amp; -> &)
        try:
            text = html_module.unescape(text)
        except Exception:
            pass
        w = _parse_svg_length(fo.get("width"), ms.foreignobject_default_width)
        h = _parse_svg_length(fo.get("height"), ms.foreignobject_default_height)
        tx, ty = _parse_svg_matrix_translate(fo.get("transform", ""))
        # foreignObject often uses matrix(1 0 0 -1 0 ty) (flip Y); center in local is (w/2, h/2) -> (tx + w/2, ty - h/2)
        cx = tx + w / 2
        cy = ty - h / 2
        font_size = max(ms.font_size_min, min(24.0, h * ms.font_size_max_ratio))
        text_elem = soup.new_tag("text", x=f"{cx:.2f}", y=f"{cy:.2f}")
        text_elem["text-anchor"] = "middle"
        text_elem["dominant-baseline"] = "middle"
        text_elem["font-size"] = f"{font_size:.1f}"
        text_elem["fill"] = fo.get("color") or "#000000"
        text_elem.string = text
        fo.replace_with(text_elem)

    return str(svg_elem)


def _parse_svg_length(s: str | None, default: float) -> float:
    """Parse SVG length (number or number+unit) to float."""
    if not s:
        return default
    s = str(s).strip()
    m = re.match(r"^([\d.-]+)", s)
    return float(m.group(1)) if m else default


def _parse_svg_matrix_translate(transform: str) -> tuple[float, float]:
    """Parse matrix(a b c d e f) and return (e, f) as (tx, ty)."""
    if not transform:
        return 0.0, 0.0
    m = _MATRIX_RE.search(transform)
    if m:
        return float(m.group(5)), float(m.group(6))
    return 0.0, 0.0


def _collect_figure_images_before_caption(figure: Tag) -> list[Tag]:
    """Collect ``<img>`` nodes in document order before the caption element (ar5iv multi-panel).

    LaTeXML often emits one outer ``figure`` with nested ``figure.ltx_figure_panel`` per
    subfigure; all panels share one ``figcaption``. Using only ``figure.find('img')``
    would take the first raster and skip the rest, and ``figure_counter`` would advance
    by one per float instead of per ``\\includegraphics``, desynchronizing ``image_map``.
    """
    cap = figure.find("figcaption") or figure.find("span", class_=re.compile(r"ltx_caption"))
    imgs: list[Tag] = []
    for el in figure.descendants:
        if cap is not None and el is cap:
            break
        if getattr(el, "name", None) == "img":
            imgs.append(el)
    if not imgs:
        imgs = list(figure.find_all("img"))
    if not imgs:
        img = figure.find("img")
        if img is None:
            prev = figure.find_previous_sibling()
            if isinstance(prev, Tag):
                img = prev.find("img")
        if img is not None:
            imgs = [img]
    return imgs


def _format_figure_raster_block(raster_paths: list[tuple[str, str]]) -> str:
    """Format raster paths for Markdown output.

    A single image uses ``![alt](path)``. Two or more are emitted as a centered HTML
    row so panels sit side-by-side in viewers that allow raw HTML.
    """
    if not raster_paths:
        return ""
    if len(raster_paths) == 1:
        p, alt = raster_paths[0]
        return f"![{alt}]({p})"
    n = len(raster_paths)
    if n == 2:
        width = "45%"
    elif n == 3:
        width = "31%"
    elif n == 4:
        width = "22%"
    else:
        pct = max(14, min(90 // n, 45))
        width = f"{pct}%"
    lines = ['<div align="center">']
    for p, alt in raster_paths:
        src_esc = html_module.escape(p, quote=True)
        alt_esc = html_module.escape(alt, quote=True)
        lines.append(f'  <img src="{src_esc}" width="{width}" alt="{alt_esc}" />')
    lines.append("</div>")
    return "\n".join(lines)


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


def _is_ltx_listing_container(tag: Tag) -> bool:
    """Outer ``div.ltx_listing`` (not ``ltx_listingline`` rows)."""
    if tag.name != "div":
        return False
    cls = tag.get("class", [])
    return "ltx_listing" in cls and "ltx_listingline" not in cls


def _serialize_listing_line(line: Tag, *, remove_inline_citations: bool = False) -> str:
    """One ``ltx_listingline`` row: walk children so spans/em/math serialize (not dropped by ``_serialize_children``)."""
    parts: list[str] = []
    for child in line.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            parts.append(_serialize_inline(child, remove_inline_citations=remove_inline_citations))
    return "".join(parts).rstrip()


def _serialize_ltx_listing_div(tag: Tag, *, remove_inline_citations: bool = False) -> str:
    """``div.ltx_listing``: prefer base64 payload in ``ltx_listing_data``; else join ``ltx_listingline`` rows."""
    cls = " ".join(tag.get("class", []))
    data = tag.find("div", class_=re.compile(r"ltx_listing_data"))
    if data:
        a = data.find("a", href=re.compile(r"^data:text/plain"))
        if a and a.get("href"):
            decoded = _decode_data_plain_href(a["href"])
            if decoded is not None:
                lang = "text"
                m = re.search(r"ltx_lst_language_(\w+)", cls)
                if m:
                    lang = m.group(1).lower()
                return f"```{lang}\n{decoded.rstrip()}\n```"
    lines_out: list[str] = []
    for line in tag.find_all("div", class_=re.compile(r"ltx_listingline"), recursive=False):
        raw = _serialize_listing_line(line, remove_inline_citations=remove_inline_citations)
        lines_out.append(raw)
    if lines_out:
        body = "\n".join(lines_out).rstrip()
        return f"```text\n{body}\n```"
    return ""


def _consume_raster_image_path(
    src: str | None,
    *,
    image_map: dict[int, Path] | None,
    image_stem_map: dict[str, Path] | None,
    used_indices: set[int],
) -> Path | None:
    """Map ``<img src>`` to a processed asset without relying on HTML/TeX DOM order.

    When the title (or other stripped blocks) contains ``\\includegraphics``, TeX raster
    order can differ from the figure order left in the body. Positional
    ``image_map[0], image_map[1], ...`` then pairs the wrong file with each caption.

    Resolution order:

    1. Match ``src`` basename/stem via ``image_stem_map`` (same as
       :func:`_resolve_image_by_html_src`). If a path is found, also mark the
       corresponding ``image_map`` index as used when filenames align.
    2. Otherwise take the **smallest unused** index in ``image_map`` (sequential fallback).
    """
    path = _resolve_image_by_html_src(src, image_stem_map)
    if path is not None:
        if image_map is not None:
            for idx in sorted(image_map.keys()):
                if idx in used_indices:
                    continue
                p = image_map[idx]
                if p.name == path.name or p == path:
                    used_indices.add(idx)
                    break
        return path

    if image_map is None:
        return None
    available = sorted(i for i in image_map if i not in used_indices)
    if not available:
        return None
    idx = available[0]
    used_indices.add(idx)
    return image_map[idx]


def _resolve_image_by_html_src(
    src: str | None,
    stem_map: dict[str, Path] | None,
) -> Path | None:
    """Match arXiv HTML ``<img src=...>`` to a processed TeX asset by filename.

    DOM order of figures can differ from ``\\includegraphics`` order in the TeX
    source; positional ``image_map[index]`` then pairs the wrong file with a
    caption. Keys in ``stem_map`` are TeX/source stems and output basenames (see
    :func:`arxiv2md_beta.images.resolver.process_images`).
    """
    if not src or not stem_map:
        return None
    path = unquote(urlparse(src.strip()).path)
    if not path:
        return None
    basename = Path(path).name
    stem = Path(basename).stem
    if stem in stem_map:
        return stem_map[stem]
    if basename in stem_map:
        return stem_map[basename]
    stem_lower = stem.lower()
    for k, v in stem_map.items():
        if k.lower() == stem_lower:
            return v
    base_lower = basename.lower()
    for k, v in stem_map.items():
        if k.lower() == base_lower:
            return v
    for k, v in stem_map.items():
        if v.name.lower() == base_lower:
            return v
    return None


def convert_html_to_markdown(
    html: str,
    *,
    remove_refs: bool = False,
    remove_toc: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    images_dir: Path | None = None,
) -> str:
    """Convert arXiv HTML into Markdown.

    Parameters
    ----------
    html : str
        HTML content
    remove_refs : bool
        Remove bibliography sections
    remove_toc : bool
        Remove table of contents
    image_map : dict[int, Path] | None
        Mapping from figure index (0-based) to local image path
    image_stem_map : dict[str, Path] | None
        TeX/source stem and output basename to processed path (preferred over index)
    """
    soup = BeautifulSoup(html, "html.parser")
    toc_markdown = None
    toc_nav = soup.find("nav", class_=re.compile(r"ltx_TOC"))
    if toc_nav and not remove_toc:
        toc_markdown = _serialize_toc(toc_nav)

    _strip_unwanted_elements(soup)
    if remove_refs:
        for ref in soup.find_all("section", class_=re.compile(r"ltx_bibliography")):
            ref.decompose()

    convert_all_mathml_to_latex(soup)
    fix_tabular_tables(soup)

    root = _find_document_root(soup)
    title_tag = root.find("h1", class_=re.compile(r"ltx_title_document"))
    authors_tag = root.find("div", class_=re.compile(r"ltx_authors"))
    abstract_tag = root.find("div", class_=re.compile(r"ltx_abstract"))

    blocks: list[str] = []
    if title_tag:
        blocks.append(f"# {_normalize_text(title_tag.get_text(' ', strip=True))}")
    if authors_tag:
        authors_text = _normalize_text(authors_tag.get_text(" ", strip=True))
        if authors_text:
            blocks.append(f"Authors: {authors_text}")
    if toc_markdown:
        blocks.append("## Contents\n" + toc_markdown)
    if abstract_tag:
        blocks.extend(_serialize_abstract(abstract_tag))

    for tag in (title_tag, authors_tag, abstract_tag):
        if tag:
            tag.decompose()

    blocks.extend(
        _serialize_children(
            root,
            image_map=image_map,
            image_stem_map=image_stem_map,
            images_dir=images_dir,
        )
    )

    return "\n\n".join(block for block in blocks if block).strip()


def convert_fragment_to_markdown(
    html: str,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> str:
    """Convert an HTML fragment into Markdown without title/author/abstract handling.

    Parameters
    ----------
    html : str
        The HTML fragment to convert.
    remove_inline_citations : bool
        If True, completely remove inline citation links. If False (default),
        citation links are converted to plain text (URL stripped).
    image_map : dict[int, Path] | None
        Mapping from figure index (0-based) to local image path
    image_stem_map : dict[str, Path] | None
        TeX/source stem and output basename to processed path (preferred over index)
    figure_counter : list[int] | None
        Shared counter for image figures across sections (mutated in place)
    """
    soup = BeautifulSoup(html, "html.parser")
    _strip_unwanted_elements(soup)
    convert_all_mathml_to_latex(soup)
    fix_tabular_tables(soup)
    blocks = _serialize_children(
        soup,
        remove_inline_citations=remove_inline_citations,
        image_map=image_map,
        image_stem_map=image_stem_map,
        figure_counter=figure_counter,
        images_dir=images_dir,
    )
    return "\n\n".join(block for block in blocks if block).strip()


def _find_document_root(soup: BeautifulSoup) -> Tag:
    root = soup.find("article", class_=re.compile(r"ltx_document"))
    if root:
        return root
    if soup.body:
        return soup.body
    return soup


def _strip_unwanted_elements(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()
    for tag in soup.select("nav.ltx_page_navbar, nav.ltx_TOC"):
        tag.decompose()
    for tag in soup.select("button.sr-only, div.package-alerts, div.ltx_pagination, footer"):
        tag.decompose()


def convert_all_mathml_to_latex(root: BeautifulSoup) -> None:
    for math in root.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": "application/x-tex"})
        if annotation and annotation.text:
            latex_source = annotation.text.strip()
            latex_source = _LATEX_COMMENT_RE.sub("", latex_source)
            latex_source = _LATEX_UNDERSCORE_RE.sub(r"\1", latex_source)
            latex_source = _LATEX_BRACKET_RE.sub("", latex_source)
            math.replace_with(f"${latex_source}$")
        else:
            math.replace_with(math.get_text(" ", strip=True))


def fix_tabular_tables(root: BeautifulSoup) -> None:
    tables = root.find_all("table", class_=re.compile(r"ltx_tabular"))
    for table in tables:
        _remove_all_attributes(table)
        for child in table.find_all(["tbody", "thead", "tfoot", "tr", "td", "th"]):
            _remove_all_attributes(child)


def _remove_all_attributes(tag: Tag) -> None:
    tag.attrs = {}


def _serialize_children(
    container: Tag,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    figure_counter: list[int] | None = None,
    used_image_indices: set[int] | None = None,
    images_dir: Path | None = None,
) -> list[str]:
    """Serialize children with figure counter tracking."""
    if figure_counter is None:
        figure_counter = [0]  # Use list to allow mutation in nested calls
    if used_image_indices is None:
        used_image_indices = set()

    blocks: list[str] = []
    for child in container.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        blocks.extend(
            _serialize_block(
                child,
                remove_inline_citations=remove_inline_citations,
                image_map=image_map,
                image_stem_map=image_stem_map,
                figure_counter=figure_counter,
                used_image_indices=used_image_indices,
                images_dir=images_dir,
            )
        )
    return blocks


def _serialize_block(
    tag: Tag,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    figure_counter: list[int] | None = None,
    used_image_indices: set[int] | None = None,
    images_dir: Path | None = None,
) -> list[str]:
    if figure_counter is None:
        figure_counter = [0]
    if used_image_indices is None:
        used_image_indices = set()

    # Handle span/div.ltx_figure (ar5iv uses this in abstract instead of <figure>)
    if tag.name in {"span", "div"} and "ltx_figure" in " ".join(tag.get("class", [])):
        img = tag.find("img")
        if (
            img is not None
            and "ltx_table" not in " ".join(tag.get("class", []))
            and "ltx_float_algorithm" not in " ".join(tag.get("class", []))
        ):
            figure = _serialize_figure(
                tag,
                used_image_indices=used_image_indices,
                remove_inline_citations=remove_inline_citations,
                image_map=image_map,
                image_stem_map=image_stem_map,
                figure_counter=figure_counter,
                consume_image_slots=True,
                images_dir=images_dir,
            )
            return [figure] if figure else []

    if tag.name == "div" and _is_ltx_listing_container(tag):
        md = _serialize_ltx_listing_div(tag, remove_inline_citations=remove_inline_citations)
        return [md] if md else []

    # Handle div with role="paragraph" (common in Science.org HTML)
    if tag.name == "div" and tag.get("role") == "paragraph":
        content = _normalize_text(_serialize_inline(tag, remove_inline_citations=remove_inline_citations))
        if content:
            return [content]
        return []

    if tag.name in {"section", "article", "div", "span"}:
        return _serialize_children(
            tag,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            used_image_indices=used_image_indices,
            images_dir=images_dir,
        )

    if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag.name[1])
        heading = _normalize_text(tag.get_text(" ", strip=True))
        if not heading:
            return []
        return [f"{'#' * level} {heading}"]

    if tag.name == "p":
        return _serialize_paragraph_maybe_with_figures(
            tag,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            used_image_indices=used_image_indices,
            images_dir=images_dir,
        )

    if tag.name in {"ul", "ol"}:
        lines = _serialize_list(tag, remove_inline_citations=remove_inline_citations)
        return ["\n".join(lines)] if lines else []

    if tag.name == "figure":
        # Only use image_map index for image figures (has img, not table/algorithm)
        img = tag.find("img")
        if img is None:
            prev = tag.find_previous_sibling()
            if isinstance(prev, Tag):
                img = prev.find("img")
        fc = " ".join(tag.get("class", []))
        is_image_figure = (
            img is not None
            and "ltx_table" not in fc
            and "ltx_float_algorithm" not in fc
            and "ltx_algorithm" not in fc
        )
        figure = _serialize_figure(
            tag,
            used_image_indices=used_image_indices,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            image_stem_map=image_stem_map,
            figure_counter=figure_counter,
            consume_image_slots=is_image_figure,
            images_dir=images_dir,
        )
        return [figure] if figure else []

    if tag.name == "table":
        table_md = _serialize_table(tag, remove_inline_citations=remove_inline_citations)
        return [table_md] if table_md else []

    if tag.name == "blockquote":
        content = _normalize_text(_serialize_inline(tag, remove_inline_citations=remove_inline_citations))
        if not content:
            return []
        return ["> " + content]

    if tag.name == "br":
        return []

    return _serialize_children(
        tag,
        remove_inline_citations=remove_inline_citations,
        image_map=image_map,
        image_stem_map=image_stem_map,
        figure_counter=figure_counter,
        used_image_indices=used_image_indices,
        images_dir=images_dir,
    )


def _serialize_abstract(tag: Tag) -> list[str]:
    blocks = ["## Abstract"]
    paragraphs = tag.find_all("p")
    if not paragraphs:
        content = _normalize_text(tag.get_text(" ", strip=True))
        if content:
            blocks.append(content)
        return blocks

    for paragraph in paragraphs:
        text = _serialize_paragraph(paragraph)
        if text:
            blocks.append(text)
    return blocks


def _serialize_paragraph(tag: Tag, *, remove_inline_citations: bool = False) -> str:
    content = _serialize_inline(tag, remove_inline_citations=remove_inline_citations)
    content = _cleanup_inline_text(content)
    return content


def _serialize_paragraph_maybe_with_figures(
    tag: Tag,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    figure_counter: list[int] | None = None,
    used_image_indices: set[int] | None = None,
    images_dir: Path | None = None,
) -> list[str]:
    """Serialize a <p> tag, splitting out embedded span.ltx_figure as block-level figures."""
    if figure_counter is None:
        figure_counter = [0]
    if used_image_indices is None:
        used_image_indices = set()
    blocks: list[str] = []
    current_text_parts: list[Tag | NavigableString] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            current_text_parts.append(child)
            continue
        if isinstance(child, Tag):
            is_fig = (
                child.name in {"span", "div"}
                and "ltx_figure" in " ".join(child.get("class", []))
                and child.find("img") is not None
                and "ltx_table" not in " ".join(child.get("class", []))
                and "ltx_float_algorithm" not in " ".join(child.get("class", []))
            )
            if is_fig:
                # Flush accumulated text
                if current_text_parts:
                    text_content = "".join(
                        _serialize_inline(c, remove_inline_citations=remove_inline_citations)
                        for c in current_text_parts
                    )
                    para = _cleanup_inline_text(text_content)
                    if para:
                        blocks.append(para)
                    current_text_parts = []
                # Serialize figure
                fig_md = _serialize_figure(
                    child,
                    used_image_indices=used_image_indices,
                    remove_inline_citations=remove_inline_citations,
                    image_map=image_map,
                    image_stem_map=image_stem_map,
                    figure_counter=figure_counter,
                    consume_image_slots=True,
                    images_dir=images_dir,
                )
                if fig_md:
                    blocks.append(fig_md)
            else:
                current_text_parts.append(child)
    if current_text_parts:
        text_content = "".join(
            _serialize_inline(c, remove_inline_citations=remove_inline_citations)
            for c in current_text_parts
        )
        para = _cleanup_inline_text(text_content)
        if para:
            blocks.append(para)
    return blocks


def _is_citation_link(href: str | None) -> bool:
    """Check if a link is a citation reference (e.g., #bib.bib7, #core-collateral-R1).

    Handles:
    - arXiv bib links: #bib.bib7, #bib8
    - Science.org collateral links: #core-collateral-R1
    - Other external DOI citation links
    """
    if not href:
        return False
    # arXiv bib links
    if "#bib." in href or href.startswith("#bib"):
        return True
    # Science.org and similar collateral/footnote links
    if "#core-collateral-R" in href:
        return True
    # Other citation patterns (e.g., #ref1, #citation-1, etc.)
    if _CITE_HREF_RE.search(href):
        return True
    return False


def _is_internal_paper_link(href: str | None) -> bool:
    """Check if a link is an internal paper section reference (e.g., arxiv.org/html/...#S2.SS1)."""
    if not href:
        return False
    return "arxiv.org/html/" in href and "#" in href and "#bib" not in href


def _is_local_fragment_link(href: str | None) -> bool:
    """Check if a link is a local arXiv-style fragment reference (e.g. #S2, #S4.SS1)."""
    if not href:
        return False
    return href.startswith("#") and not href.startswith("#bib")


def _map_arxiv_fragment_to_anchor(fragment: str) -> str | None:
    """Map arXiv fragment key to local markdown anchor."""
    frag = fragment.strip()
    if not frag:
        return None
    # Figure: S1.F1, S5.F7 -> figure-1, figure-7
    m = _FIGURE_FRAG_RE.match(frag)
    if m:
        return f"#figure-{m.group(1)}"
    # Table: S5.T1, A2.T3 -> table-1, table-3
    m = _TABLE_FRAG_RE.match(frag)
    if m:
        return f"#table-{m.group(1)}"
    # Appendix: A1, A2, A3 -> appendix-a, appendix-b, appendix-c
    m = _APPENDIX_FRAG_RE.match(frag)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 26:
            return f"#appendix-{chr(96 + n)}"
    # Algorithm: alg1, alg2 -> algorithm-1, algorithm-2
    m = _ALG_FRAG_RE.match(frag)
    if m:
        return f"#algorithm-{m.group(1)}"
    # Section: S1 -> section-1
    m = _SECTION_FRAG_RE.match(frag)
    if m:
        return f"#section-{m.group(1)}"
    # Subsection: S4.SS1, S5.SS2 -> section-4-1, section-5-2
    m = _SUBSECTION_FRAG_RE.match(frag)
    if m:
        return f"#section-{m.group(1)}-{m.group(2)}"
    return None


def _arxiv_fragment_to_anchor(href: str | None) -> str | None:
    """Convert arxiv HTML fragment to local markdown anchor.

    Maps arxiv.org/html/...#S1.F1 -> #figure-1, #S5.T1 -> #table-1,
    #A1 -> #appendix-a, #alg1 -> #algorithm-1, #S4.SS2 -> #section-4-2, etc.
    """
    if not href or "arxiv.org/html/" not in href or "#" not in href or "#bib" in href:
        return None
    frag = href.split("#")[-1].strip()
    return _map_arxiv_fragment_to_anchor(frag)


def _serialize_inline(node: Tag | NavigableString, *, remove_inline_citations: bool = False) -> str:
    if isinstance(node, NavigableString):
        return str(node)

    if node.name == "br":
        return "\n"

    if node.name in {"em", "i"}:
        return f"*{_serialize_children_inline(node, remove_inline_citations=remove_inline_citations)}*"

    if node.name in {"strong", "b"}:
        return f"**{_serialize_children_inline(node, remove_inline_citations=remove_inline_citations)}**"

    if node.name == "a":
        text = _serialize_children_inline(node, remove_inline_citations=remove_inline_citations).strip()
        href = node.get("href")
        # Handle citation links specially
        if _is_citation_link(href):
            if remove_inline_citations:
                return ""  # Completely remove citation
            # Format citation as [N] instead of *N*
            citation_text = node.get_text(strip=True)
            return f"[{citation_text}]"
        # Handle internal paper links: replace with local markdown anchor
        if _is_internal_paper_link(href):
            local_anchor = _arxiv_fragment_to_anchor(href)
            if local_anchor:
                return f"[{text or href}]({local_anchor})"
            if remove_inline_citations:
                return text
        # Handle local arXiv-like links: #S2, #S4.SS1, #alg1 ...
        if _is_local_fragment_link(href):
            local_anchor = _map_arxiv_fragment_to_anchor(href[1:])
            if local_anchor:
                return f"[{text or href}]({local_anchor})"
        # Regular links: keep full markdown link
        if href:
            return f"[{text or href}]({href})"
        return text

    if node.name == "sup":
        text = _serialize_children_inline(node, remove_inline_citations=remove_inline_citations).strip()
        return f"^{text}" if text else ""

    if node.name == "cite":
        return _serialize_children_inline(node, remove_inline_citations=remove_inline_citations)

    if node.name == "math":
        text = node.get_text(" ", strip=True)
        return f"${text}$" if text else ""

    if "ltx_note" in node.get("class", []):
        text = _normalize_text(_serialize_children_inline(node, remove_inline_citations=remove_inline_citations))
        return f"({text})" if text else ""

    return _serialize_children_inline(node, remove_inline_citations=remove_inline_citations)


def _serialize_children_inline(tag: Tag, *, remove_inline_citations: bool = False) -> str:
    return "".join(_serialize_inline(child, remove_inline_citations=remove_inline_citations) for child in tag.children)


def _cleanup_inline_text(text: str) -> str:
    # Collapse multiple whitespace (including newlines) to single space
    # This fixes unwanted line breaks within paragraphs
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _serialize_list(list_tag: Tag, indent: int = 0, *, remove_inline_citations: bool = False) -> list[str]:
    lines: list[str] = []
    for item in list_tag.find_all("li", recursive=False):
        item_text_parts: list[str] = []
        nested_lists: list[Tag] = []
        for child in item.children:
            if isinstance(child, Tag) and child.name in {"ul", "ol"}:
                nested_lists.append(child)
            else:
                item_text_parts.append(_serialize_inline(child, remove_inline_citations=remove_inline_citations))
        item_text = _cleanup_inline_text("".join(item_text_parts))
        prefix = "  " * indent + "- "
        lines.append(prefix + item_text if item_text else prefix.rstrip())
        for nested in nested_lists:
            lines.extend(_serialize_list(nested, indent + 1, remove_inline_citations=remove_inline_citations))
    return lines


def _serialize_toc(toc_nav: Tag) -> str:
    list_tag = toc_nav.find("ol")
    if not list_tag:
        return ""
    lines = _serialize_list(list_tag)
    return "\n".join(lines)


def _is_ltx_tabular_row(tag: Tag) -> bool:
    return bool(tag.get("class")) and "ltx_tr" in " ".join(tag.get("class", []))


def _is_ltx_tabular_cell(tag: Tag) -> bool:
    cc = " ".join(tag.get("class", []))
    return "ltx_td" in cc or "ltx_th" in cc


def _rows_from_ltx_span_tabular(
    tabular: Tag,
    *,
    remove_inline_citations: bool = False,
) -> list[list[str]]:
    """Extract grid rows from ar5iv/LaTeXML ``span.ltx_tabular`` (not real HTML tables)."""
    rows: list[list[str]] = []

    def append_row(row: Tag) -> None:
        vals: list[str] = []
        for cell in row.children:
            if not isinstance(cell, Tag) or not _is_ltx_tabular_cell(cell):
                continue
            cell_text = _cleanup_inline_text(
                _serialize_inline(cell, remove_inline_citations=remove_inline_citations)
            ).replace("\n", "<br>")
            vals.append(cell_text)
        if vals:
            rows.append(vals)

    # Rows directly under ltx_tabular (uncommon)
    for child in tabular.children:
        if isinstance(child, Tag) and _is_ltx_tabular_row(child):
            append_row(child)
    if rows:
        return rows

    # thead / tbody / tfoot with ltx_tr rows (typical ar5iv output)
    for child in tabular.children:
        if not isinstance(child, Tag):
            continue
        cc = " ".join(child.get("class", []))
        if not _TABLE_PART_RE.search(cc):
            continue
        for row in child.children:
            if isinstance(row, Tag) and _is_ltx_tabular_row(row):
                append_row(row)

    return rows


def _pipe_table_from_rows(rows: list[list[str]]) -> str:
    """Build GitHub-flavored markdown pipe table from cell rows."""
    if not rows:
        return ""
    max_cols = max(len(row) for row in rows)
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _serialize_table(table: Tag, *, remove_inline_citations: bool = False) -> str:
    classes = " ".join(table.get("class", []))
    if _EQUATION_TABLE_RE.search(classes):
        eqn_text = _normalize_text(table.get_text(" ", strip=True))
        if not eqn_text:
            return ""
        # Fix: convert_all_mathml replaces math with $formula$; eqn number is separate.
        # Pattern "$formula$ (n)" -> "formula(n)" for correct $$ formula(n) $$
        eqn_match = _INLINE_MATH_RE.match(eqn_text.strip())
        if eqn_match:
            eqn_text = f"{eqn_match.group(1)}({eqn_match.group(2)})"
        else:
            # Fallback: formula may contain $ from ar5iv annotations; strip outer $ and extract (n)
            stripped = eqn_text.strip()
            num_match = _EQN_TRAIL_NUM_RE.search(stripped)
            if num_match:
                body = stripped[: num_match.start()].strip()
                num = num_match.group(1)
                if len(body) >= 2 and body.startswith("$") and body.endswith("$"):
                    body = body[1:-1]  # Strip outer $, preserve inner $
                eqn_text = f"{body}({num})"
            else:
                if len(stripped) >= 2 and stripped.startswith("$") and stripped.endswith("$"):
                    eqn_text = stripped[1:-1]
                else:
                    eqn_text = _DISPLAY_MATH_DOLLAR_RE.sub(r"\1", stripped)
        # Escape $ inside $$ block so markdown doesn't parse as inline math
        eqn_text = _sanitize_display_math(eqn_text)
        return f"$$\n{eqn_text}\n$$"

    rows = []
    # Find rows in tbody, thead, tfoot, or directly in table
    # Handle nested structure where rows might be inside tbody/thead/tfoot
    tbody_elements = table.find_all(["tbody", "thead", "tfoot"], recursive=False)
    
    if tbody_elements:
        # Table has tbody/thead/tfoot structure - find rows within them
        for tbody in tbody_elements:
            for row in tbody.find_all("tr", recursive=False):
                cells = row.find_all(["th", "td"], recursive=False)
                if not cells:
                    continue
                values = []
                for cell in cells:
                    cell_text = _cleanup_inline_text(_serialize_inline(cell, remove_inline_citations=remove_inline_citations)).replace("\n", "<br>")
                    values.append(cell_text)
                rows.append(values)
    else:
        # Table has no tbody/thead/tfoot - find rows directly in table
        for row in table.find_all("tr", recursive=False):
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            values = []
            for cell in cells:
                cell_text = _cleanup_inline_text(_serialize_inline(cell, remove_inline_citations=remove_inline_citations)).replace("\n", "<br>")
                values.append(cell_text)
                rows.append(values)

    return _pipe_table_from_rows(rows)


def _find_tabular_in_figure(figure: Tag) -> Tag | None:
    """Locate tabular content: HTML ``<table>`` or ar5iv ``span``/``div.ltx_tabular``."""
    t = figure.find("table")
    if t is not None:
        return t
    for name in ("span", "div"):
        el = figure.find(name, class_=re.compile(r"(^|\s)ltx_tabular(\s|$)"))
        if el is not None:
            return el
    return None


def _serialize_tabular_node(tag: Tag, *, remove_inline_citations: bool = False) -> str:
    """Serialize either a real ``<table>`` or an ar5iv ``span``/``div.ltx_tabular`` grid."""
    if tag.name == "table":
        return _serialize_table(tag, remove_inline_citations=remove_inline_citations)
    classes = " ".join(tag.get("class", []))
    if "ltx_tabular" in classes and tag.name in ("span", "div"):
        rows = _rows_from_ltx_span_tabular(tag, remove_inline_citations=remove_inline_citations)
        return _pipe_table_from_rows(rows)
    return ""


def _serialize_figure(
    figure: Tag,
    *,
    used_image_indices: set[int],
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    image_stem_map: dict[str, Path] | None = None,
    figure_counter: list[int] | None = None,
    consume_image_slots: bool = True,
    images_dir: Path | None = None,
) -> str:
    """Serialize figure with image map support (multi-panel: multiple ``<img>`` per float).

    Parameters
    ----------
    figure : Tag
        Figure HTML tag
    remove_inline_citations : bool
        Remove inline citations
    image_map : dict[int, Path] | None
        Mapping from TeX raster index (0-based) to local image path
    image_stem_map : dict[str, Path] | None
        If set, ``<img src>`` basename is matched to TeX assets before using ``image_map``.
    figure_counter : list[int] | None
        Mutable shared counter incremented once per emitted raster (for tests / diagnostics).
    used_image_indices : set[int]
        TeX ``image_map`` indices already paired with a figure; fallback uses the smallest
        unused index (fixes title-strip / DOM order skew vs ``\\includegraphics`` order).
    consume_image_slots : bool
        If False, do not advance ``figure_counter`` (e.g. algorithm floats, or non-image figures).
    """
    if figure_counter is None:
        figure_counter = [0]

    figure_classes = " ".join(figure.get("class", []))
    is_table_figure = "ltx_table" in figure_classes
    is_algorithm_figure = "ltx_float_algorithm" in figure_classes or "ltx_algorithm" in figure_classes

    caption_tag = figure.find("figcaption") or figure.find("span", class_=re.compile(r"ltx_caption"))
    caption = _normalize_text(_serialize_inline(caption_tag, remove_inline_citations=remove_inline_citations)) if caption_tag else ""

    lines: list[str] = []

    if is_table_figure:
        tabular = _find_tabular_in_figure(figure)
        if tabular:
            table_md = _serialize_tabular_node(tabular, remove_inline_citations=remove_inline_citations)
            m = _TABLE_CAPTION_RE.match(caption)
            if m:
                lines.append(f'<a id="table-{m.group(1)}"></a>')
                lines.append("")
            if caption:
                lines.append(f"> {caption}")
                lines.append("")
            if table_md:
                lines.append(table_md)
        elif caption:
            lines.append(f"> Table: {caption}")
        return "\n".join(lines).strip()

    if is_algorithm_figure:
        m = _ALGORITHM_CAPTION_RE.match(caption)
        if m:
            lines.append(f'<a id="algorithm-{m.group(1)}"></a>')
            lines.append("")
        if caption:
            lines.append(f"**{caption}**")
        for listing in figure.find_all(
            "div",
            class_=lambda c: bool(c) and "ltx_listing" in c and "ltx_listingline" not in c,
        ):
            block = _serialize_ltx_listing_div(listing, remove_inline_citations=remove_inline_citations)
            if block:
                lines.append(block)
                lines.append("")
        return "\n".join(lines).strip()

    imgs = _collect_figure_images_before_caption(figure)
    svg_tag = figure.find("svg")
    svg_html = ""
    if svg_tag is not None:
        if not svg_tag.get("xmlns"):
            svg_tag["xmlns"] = "http://www.w3.org/2000/svg"
        if "viewbox" in svg_tag.attrs and "viewBox" not in svg_tag.attrs:
            svg_tag["viewBox"] = svg_tag["viewbox"]
        svg_html = str(svg_tag)

    raster_paths: list[tuple[str, str]] = []
    for img in imgs:
        src = img.get("src")
        image_path: Path | None = None
        if consume_image_slots and (image_map is not None or image_stem_map is not None):
            image_path = _consume_raster_image_path(
                src,
                image_map=image_map,
                image_stem_map=image_stem_map,
                used_indices=used_image_indices,
            )
        if image_path is not None:
            image_path_str = str(image_path)
            alt_text = Path(image_path_str).stem
            raster_paths.append((image_path_str, alt_text))
            if consume_image_slots:
                figure_counter[0] += 1

    if raster_paths:
        m = _FIGURE_CAPTION_RE.match(caption)
        if m:
            lines.append(f'<a id="figure-{m.group(1)}"></a>')
            lines.append("")
        block = _format_figure_raster_block(raster_paths)
        if block:
            lines.append(block)
            lines.append("")
        if caption:
            lines.append(f"> {caption}")
        return "\n".join(lines).strip()

    if svg_html and images_dir is not None:
        m = _FIGURE_CAPTION_RE.match(caption)
        figure_num = m.group(1) if m else None
        if figure_num:
            base_name = f"figure_{figure_num}"
        else:
            base_name = figure.get("id") or (svg_tag.get("id") if svg_tag and svg_tag.get("id") else "svg_figure")
        base_name = _SAFE_NAME_RE.sub("_", str(base_name))
        filename = base_name if base_name.lower().endswith(".svg") else f"{base_name}.svg"
        svg_path = images_dir / filename
        dup = 1
        while svg_path.exists():
            filename = f"{base_name}_{dup}.svg"
            svg_path = images_dir / filename
            dup += 1
        try:
            svg_content = _svg_replace_foreignobject_with_text(svg_html)
            if not svg_content.lstrip().startswith("<?xml"):
                svg_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg_content
            svg_path.write_text(svg_content, encoding="utf-8")
        except Exception:
            if figure_num:
                lines.append(f'<a id="figure-{figure_num}"></a>')
                lines.append("")
            lines.append(svg_html.strip())
            if caption:
                lines.append(f"> {caption}")
        else:
            if figure_num:
                lines.append(f'<a id="figure-{figure_num}"></a>')
                lines.append("")
            rel_path = Path(images_dir.name) / filename
            alt_text = Path(filename).stem
            lines.append(f"![{alt_text}]({rel_path.as_posix()})")
            lines.append("")
            if caption:
                lines.append(f"> {caption}")
        if consume_image_slots:
            figure_counter[0] += 1
        return "\n".join(lines).strip()

    img0 = imgs[0] if imgs else None
    if img0 is None:
        prev = figure.find_previous_sibling()
        if isinstance(prev, Tag):
            img0 = prev.find("img")
    src = img0.get("src") if img0 else None
    alt = img0.get("alt") if img0 else None
    if src:
        # Improved format: use markdown image syntax with caption
        img_alt = alt or (caption[:50] + "..." if len(caption) > 50 else caption) if caption else "Figure"
        lines.append(f"![{img_alt}]({src})")
        lines.append("")
        if caption:
            # Format caption as blockquote for better visual separation
            lines.append(f"> **{caption}**")
        return "\n".join(lines).strip()

    return "\n".join(lines).strip()


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()
