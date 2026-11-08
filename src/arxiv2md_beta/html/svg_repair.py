"""Repair label-less inline SVG figures by borrowing ar5iv's rendering.

arXiv's native HTML is built by a newer LaTeXML ("oxide") that sometimes
drops every TikZ node label: the ``<foreignObject>`` containers come out
empty, so the inline ``<svg class="ltx_picture">`` figures render as
shape-only pictures — in the converted Markdown *and* on arxiv.org itself.
ar5iv's older LaTeXML build of the same paper keeps the labels, and both
builds share LaTeXML's element ids (``S1.F3.pic1``), so a broken SVG can be
swapped for its ar5iv counterpart by id.

Detection is content-based: a picture SVG counts as broken when its
foreignObject/``<text>`` content carries (almost) no visible text. Plain
diagram SVGs without any label structure are left alone — there is nothing
to compare against, and rasterizing them is lossless anyway.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

# A picture SVG whose visible text is under this many words is label-less.
# Broken outputs have literally none; healthy figures have dozens of labels.
_MIN_LABEL_WORDS = 3

_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>")
_SVG_TAG_RE = re.compile(r"</?svg\b")
_ID_RE = re.compile(r"""\bid=["']([^"']+)["']""")
_MATH_RE = re.compile(r"<math[^>]*>.*?</math>", re.S)


def _visible_word_count(svg_markup: str) -> int:
    """Word count of the non-math text a browser would paint in *svg_markup*.

    MathML must be excluded: the oxide build keeps math-mode labels (unicode
    glyph + ``x-tex`` annotation) while dropping all prose labels, so counting
    them masks the breakage (a "0 English words" figure can score dozens).
    """
    text = _MATH_RE.sub(" ", svg_markup)
    text = re.sub(r"<[^>]+>", " ", text)
    return len(text.split())


def labelless_picture_svg_spans(html: str) -> list[tuple[str, int, int]]:
    """Find label-less top-level ``ltx_picture`` SVGs in *html*.

    Returns ``[(svg_id, start, end)]`` character spans (end exclusive) of the
    broken SVG elements, in document order. Nested SVGs are skipped — they
    belong to an enclosing picture and are dropped by the builder anyway.
    """
    spans: list[tuple[str, int, int]] = []
    pos = 0
    while match := _SVG_OPEN_RE.search(html, pos):
        tag = match.group(0)
        close = _find_svg_close(html, match.end())
        if close is None:
            break
        pos = close
        if "ltx_picture" not in tag:
            continue
        # Nested SVGs are never visited: the scan jumps past the enclosing
        # element's full span, so only top-level pictures are matched here.
        id_match = _ID_RE.search(tag)
        if not id_match:
            continue
        markup = html[match.start() : close]
        if _visible_word_count(markup) < _MIN_LABEL_WORDS:
            spans.append((id_match.group(1), match.start(), close))
    return spans


def _find_svg_close(html: str, search_from: int) -> int | None:
    """Character offset just past the ``</svg>`` matching an open tag.

    Depth-counting handles the nested sub-SVGs LaTeXML emits for multi-panel
    pictures. ``None`` when the markup is truncated (malformed input).
    """
    depth = 1
    pos = search_from
    closing: re.Match[str] | None = None
    while depth:
        next_tag = _SVG_TAG_RE.search(html, pos)
        if next_tag is None:
            return None
        closing = next_tag
        depth += -1 if html.startswith("</", next_tag.start()) else 1
        pos = next_tag.end()
    assert closing is not None  # the loop runs at least once (depth starts at 1)
    return html.find(">", closing.start()) + 1


def substitute_svgs_from_ar5iv(html: str, ar5iv_html: str) -> tuple[str, int]:
    """Replace label-less SVGs in *html* with their ar5iv counterparts.

    Matching is by SVG id (shared across both LaTeXML builds). An ar5iv SVG
    only substitutes when it actually carries labels — ar5iv is occasionally
    broken the same way. Returns the new HTML and the number of SVGs swapped.
    """
    spans = labelless_picture_svg_spans(html)
    if not spans:
        return html, 0
    ar5iv_by_id = _picture_svgs_by_id(ar5iv_html)
    replaced = 0
    for svg_id, start, end in reversed(spans):
        candidate = ar5iv_by_id.get(svg_id)
        if not candidate or _visible_word_count(candidate) < _MIN_LABEL_WORDS:
            continue
        html = html[:start] + candidate + html[end:]
        replaced += 1
    return html, replaced


def _picture_svgs_by_id(ar5iv_html: str) -> dict[str, str]:
    """Map ``id → markup`` for top-level ``ltx_picture`` SVGs in *ar5iv_html*."""
    soup = BeautifulSoup(ar5iv_html, "html.parser")
    by_id: dict[str, str] = {}
    for svg in soup.find_all("svg", class_="ltx_picture"):
        if svg.find_parent("svg") is not None:
            continue
        svg_id = svg.get("id")
        if svg_id:
            by_id[svg_id] = str(svg)
    return by_id
