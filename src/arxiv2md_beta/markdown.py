"""Convert arXiv HTML to Markdown with a custom serializer."""

from __future__ import annotations

import html as html_module
import re
from pathlib import Path
from typing import Iterable

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
    content = re.sub(
        r"\\raisebox\{[^}]+\}\{\\hbox to 0\.0\s*pt\{\\hss\\vbox to 0\.0\s*pt\{\\hbox\{\$([^$]*)\$\}\\vss\}\}\}",
        r"\1",
        content,
        flags=re.DOTALL,
    )
    # 2. Remove trailing $ before equation number: "$ (1)" -> "(1)"
    content = re.sub(r"\$\s*\((\d+)\)\s*$", r"(\1)", content)
    # 3. Replace $} with } (fix \hbox{...$} without breaking brace structure)
    content = content.replace("$}", "}")
    # 4. Remove all remaining unescaped $ (they break markdown $$ block parsing)
    content = re.sub(r"(?<!\\)\$", "", content)
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


def convert_html_to_markdown(
    html: str,
    *,
    remove_refs: bool = False,
    remove_toc: bool = False,
    image_map: dict[int, Path] | None = None,
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

    blocks.extend(_serialize_children(root, image_map=image_map, images_dir=images_dir))

    return "\n\n".join(block for block in blocks if block).strip()


def convert_fragment_to_markdown(
    html: str,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
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
            latex_source = re.sub(r"(?<!\\)%", "", latex_source)
            latex_source = re.sub(r"\\([_^])", r"\1", latex_source)
            latex_source = re.sub(r"\\(?=[\[\]])", "", latex_source)
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
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> list[str]:
    """Serialize children with figure counter tracking."""
    if figure_counter is None:
        figure_counter = [0]  # Use list to allow mutation in nested calls

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
                figure_counter=figure_counter,
                images_dir=images_dir,
            )
        )
    return blocks


def _serialize_block(
    tag: Tag,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> list[str]:
    if figure_counter is None:
        figure_counter = [0]

    # Handle span/div.ltx_figure (ar5iv uses this in abstract instead of <figure>)
    if tag.name in {"span", "div"} and "ltx_figure" in " ".join(tag.get("class", [])):
        img = tag.find("img")
        if (
            img is not None
            and "ltx_table" not in " ".join(tag.get("class", []))
            and "ltx_float_algorithm" not in " ".join(tag.get("class", []))
        ):
            image_index = figure_counter[0]
            figure = _serialize_figure(
                tag,
                remove_inline_citations=remove_inline_citations,
                image_map=image_map,
                figure_index=image_index,
                images_dir=images_dir,
            )
            figure_counter[0] += 1
            return [figure] if figure else []

    if tag.name in {"section", "article", "div", "span"}:
        return _serialize_children(
            tag,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            figure_counter=figure_counter,
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
            figure_counter=figure_counter,
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
        is_image_figure = (
            img is not None
            and "ltx_table" not in " ".join(tag.get("class", []))
            and "ltx_float_algorithm" not in " ".join(tag.get("class", []))
        )
        image_index = figure_counter[0] if is_image_figure else -1  # -1 = don't use image_map
        figure = _serialize_figure(
            tag,
            remove_inline_citations=remove_inline_citations,
            image_map=image_map,
            figure_index=image_index,
            images_dir=images_dir,
        )
        if is_image_figure:
            figure_counter[0] += 1
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
        figure_counter=figure_counter,
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
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> list[str]:
    """Serialize a <p> tag, splitting out embedded span.ltx_figure as block-level figures."""
    if figure_counter is None:
        figure_counter = [0]
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
                image_index = figure_counter[0]
                fig_md = _serialize_figure(
                    child,
                    remove_inline_citations=remove_inline_citations,
                    image_map=image_map,
                    figure_index=image_index,
                    images_dir=images_dir,
                )
                if fig_md:
                    blocks.append(fig_md)
                figure_counter[0] += 1
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
    """Check if a link is a citation reference (e.g., #bib.bib7)."""
    if not href:
        return False
    return "#bib." in href or href.startswith("#bib")


def _is_internal_paper_link(href: str | None) -> bool:
    """Check if a link is an internal paper section reference (e.g., arxiv.org/html/...#S2.SS1)."""
    if not href:
        return False
    return "arxiv.org/html/" in href and "#" in href and "#bib" not in href


def _arxiv_fragment_to_anchor(href: str | None) -> str | None:
    """Convert arxiv HTML fragment to local markdown anchor.

    Maps arxiv.org/html/...#S1.F1 -> #figure-1, #S5.T1 -> #table-1,
    #A1 -> #appendix-a, #alg1 -> #algorithm-1, #S4.SS2 -> #section-4-2, etc.
    """
    if not href or "arxiv.org/html/" not in href or "#" not in href or "#bib" in href:
        return None
    frag = href.split("#")[-1].strip()
    if not frag:
        return None
    # Figure: S1.F1, S5.F7 -> figure-1, figure-7
    m = re.match(r"S\d+\.F(\d+)$", frag)
    if m:
        return f"#figure-{m.group(1)}"
    # Table: S5.T1, A2.T3 -> table-1, table-3
    m = re.match(r"[SA]\d*\.?T(\d+)$", frag)
    if m:
        return f"#table-{m.group(1)}"
    # Appendix: A1, A2, A3 -> appendix-a, appendix-b, appendix-c
    m = re.match(r"A(\d+)$", frag)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 26:
            return f"#appendix-{chr(96 + n)}"
    # Algorithm: alg1, alg2 -> algorithm-1, algorithm-2
    m = re.match(r"alg(\d+)$", frag)
    if m:
        return f"#algorithm-{m.group(1)}"
    # Section: S1 -> section-1
    m = re.match(r"S(\d+)$", frag)
    if m:
        return f"#section-{m.group(1)}"
    # Subsection: S4.SS1, S5.SS2 -> section-4-1, section-5-2
    m = re.match(r"S(\d+)\.SS(\d+)$", frag)
    if m:
        return f"#section-{m.group(1)}-{m.group(2)}"
    return None


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
            return text  # Keep text only, strip URL
        # Handle internal paper links: replace with local markdown anchor
        if _is_internal_paper_link(href):
            local_anchor = _arxiv_fragment_to_anchor(href)
            if local_anchor:
                return f"[{text or href}]({local_anchor})"
            if remove_inline_citations:
                return text
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
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
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


def _serialize_table(table: Tag, *, remove_inline_citations: bool = False) -> str:
    classes = " ".join(table.get("class", []))
    if _EQUATION_TABLE_RE.search(classes):
        eqn_text = _normalize_text(table.get_text(" ", strip=True))
        if not eqn_text:
            return ""
        # Fix: convert_all_mathml replaces math with $formula$; eqn number is separate.
        # Pattern "$formula$ (n)" -> "formula(n)" for correct $$ formula(n) $$
        eqn_match = re.match(r"^\$([^$]+)\$\s*\((\d+)\)\s*$", eqn_text.strip())
        if eqn_match:
            eqn_text = f"{eqn_match.group(1)}({eqn_match.group(2)})"
        else:
            # Fallback: formula may contain $ from ar5iv annotations; strip outer $ and extract (n)
            stripped = eqn_text.strip()
            num_match = re.search(r"\s*\((\d+)\)\s*$", stripped)
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
                    eqn_text = re.sub(r"^\$([^$]*)\$", r"\1", stripped)
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


def _serialize_figure(
    figure: Tag,
    *,
    remove_inline_citations: bool = False,
    image_map: dict[int, Path] | None = None,
    figure_index: int = 0,
    images_dir: Path | None = None,
) -> str:
    """Serialize figure with image map support.

    Parameters
    ----------
    figure : Tag
        Figure HTML tag
    remove_inline_citations : bool
        Remove inline citations
    image_map : dict[int, Path] | None
        Mapping from figure index to local image path
    figure_index : int
        Current figure index (0-based). A negative value means the
        figure should not consume an entry from ``image_map`` (used
        for non-image figures such as algorithm floats or inline SVGs).
    """
    # Check figure type
    figure_classes = " ".join(figure.get("class", []))
    is_table_figure = "ltx_table" in figure_classes
    is_algorithm_figure = "ltx_float_algorithm" in figure_classes

    caption_tag = figure.find("figcaption") or figure.find("span", class_=re.compile(r"ltx_caption"))
    caption = _normalize_text(_serialize_inline(caption_tag, remove_inline_citations=remove_inline_citations)) if caption_tag else ""

    lines = []

    if is_table_figure:
        # Handle table figures - find and serialize the embedded table
        # Note: fix_tabular_tables strips attributes, so search for any table element
        table = figure.find("table")
        if table:
            table_md = _serialize_table(table, remove_inline_citations=remove_inline_citations)
            # Add anchor for internal links (e.g. Table 1: ... -> #table-1)
            m = re.match(r"Table\s+(\d+)\s*[:.]", caption, re.I)
            if m:
                lines.append(f'<a id="table-{m.group(1)}"></a>')
                lines.append("")  # Newline after tag to separate from content
            if caption:
                lines.append(f"> {caption}")  # Blockquote for table caption
                lines.append("")  # Newline after caption before table
            if table_md:
                lines.append(table_md)
        elif caption:
            # Fallback if no table found but has caption
            lines.append(f"> Table: {caption}")
    else:
        # Handle regular image figures (including inline SVG) and algorithms
        img = figure.find("img")
        if img is None:
            prev = figure.find_previous_sibling()
            if isinstance(prev, Tag):
                img = prev.find("img")
        src = img.get("src") if img else None
        alt = img.get("alt") if img else None
        svg_tag = figure.find("svg")
        if svg_tag is not None:
            # 确保导出的 SVG 作为独立文件时是合法的 SVG 文档
            if not svg_tag.get("xmlns"):
                svg_tag["xmlns"] = "http://www.w3.org/2000/svg"
            # ar5iv/HTML 中常用小写 viewbox，标准 SVG 需要 viewBox
            if "viewbox" in svg_tag.attrs and "viewBox" not in svg_tag.attrs:
                svg_tag["viewBox"] = svg_tag["viewbox"]
            svg_html = str(svg_tag)
        else:
            svg_html = ""

        # Try to use image_map if available (figure_index >= 0 for image figures only)
        if image_map and figure_index >= 0 and figure_index in image_map:
            image_path = image_map[figure_index]
            # Use relative path for markdown
            image_path_str = str(image_path)
            # Add anchor for internal links (e.g. Figure 1: ... -> #figure-1)
            m = re.match(r"Figure\s+(\d+)\s*[:.]", caption, re.I)
            if m:
                lines.append(f'<a id="figure-{m.group(1)}"></a>')
                lines.append("")  # Newline after tag to separate from content
            # Alt text: filename stem (e.g. fig1_v4); caption goes in blockquote below
            alt_text = Path(image_path_str).stem
            lines.append(f"![{alt_text}]({image_path_str})")
            lines.append("")  # Newline after image before caption
            if caption:
                lines.append(f"> {caption}")
        elif svg_html and images_dir is not None:
            # Inline SVG figure (no external image file). Save the SVG to the images
            # directory and reference it from Markdown.
            m = re.match(r"Figure\s+(\d+)\s*[:.]", caption, re.I)
            figure_num = m.group(1) if m else None
            base_name = None
            if figure_num:
                base_name = f"figure_{figure_num}"
            else:
                base_name = figure.get("id") or (svg_tag.get("id") if svg_tag and svg_tag.get("id") else "svg_figure")
            # Sanitize filename
            base_name = re.sub(r"[^A-Za-z0-9_.-]", "_", base_name)
            filename = base_name if base_name.lower().endswith(".svg") else f"{base_name}.svg"
            svg_path = images_dir / filename
            counter = 1
            while svg_path.exists():
                filename = f"{base_name}_{counter}.svg"
                svg_path = images_dir / filename
                counter += 1
            try:
                # 将 foreignObject（内嵌 HTML 文字）转为 SVG <text>，否则作为图片加载时文字不显示
                svg_content = _svg_replace_foreignobject_with_text(svg_html)
                # 为独立 SVG 文件添加 XML 声明，提升兼容性
                if not svg_content.lstrip().startswith("<?xml"):
                    svg_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg_content
                svg_path.write_text(svg_content, encoding="utf-8")
            except Exception:
                # If writing fails, fall back to inlining the SVG
                if figure_num:
                    lines.append(f'<a id="figure-{figure_num}"></a>')
                    lines.append("")  # Newline after tag to separate from content
                lines.append(svg_html.strip())
                if caption:
                    lines.append(f"> {caption}")
            else:
                # Add anchor for internal links (e.g. Figure 1: ... -> #figure-1)
                if figure_num:
                    lines.append(f'<a id="figure-{figure_num}"></a>')
                    lines.append("")  # Newline after tag to separate from content
                rel_path = Path(images_dir.name) / filename
                alt_text = Path(filename).stem
                lines.append(f"![{alt_text}]({rel_path.as_posix()})")
                lines.append("")  # Newline after image before caption
                if caption:
                    lines.append(f"> {caption}")
        elif is_algorithm_figure:
            # Algorithm figure: add anchor and serialize as markdown list
            # Structure: ltx_listing contains ltx_listingline divs (one per algorithm line)
            m = re.match(r"Algorithm\s+(\d+)\s*[:.\s]", caption, re.I)
            if m:
                lines.append(f'<a id="algorithm-{m.group(1)}"></a>')
                lines.append("")  # Newline after tag to separate from content
            if caption:
                lines.append(f"**{caption}**")
            # Find listing container(s) and extract each line as list item
            for listing in figure.find_all("div", class_=re.compile(r"ltx_algorithm|ltx_listing")):
                # Skip if this is a nested listing (e.g. inside another)
                if listing.find_parent("div", class_=re.compile(r"ltx_algorithm|ltx_listing")):
                    continue
                line_divs = listing.find_all("div", class_=re.compile(r"ltx_listingline"))
                if line_divs:
                    # Each ltx_listingline -> markdown list item (- line_content)
                    for line_div in line_divs:
                        line_text = _normalize_text(line_div.get_text(" ", strip=True))
                        if line_text:
                            lines.append(f"- {line_text}")
                else:
                    # Fallback: single block as code
                    block_text = _normalize_text(listing.get_text(" ", strip=True))
                    if block_text:
                        lines.append(f"```\n{block_text}\n```")
        elif src:
            # Fallback to original behavior
            if caption:
                lines.append(f"Figure: {caption}")
            image_label = alt or "Image"
            lines.append(f"{image_label}: {src}")

    return "\n".join(lines).strip()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
