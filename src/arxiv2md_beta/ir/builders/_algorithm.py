"""Pure-function algorithm helpers for HTMLBuilder.

HTMLBuilder 使用的模块级纯函数与正则常量：
不含 builder 状态、不做任何 IO（网络 / 文件），仅做数据变换。
"""

from __future__ import annotations

import ast
import base64
import re
from collections.abc import Callable
from typing import Any

from bs4 import Tag

from arxiv2md_beta.ir.blocks import BlockUnion, ListIR
from arxiv2md_beta.ir.builders._math_norm import (
    MBOX_TO_TEXT_RE,
    NOLINEBREAK_RE,
    PERP_IN_MATH_RE,
    PERP_REPLACEMENT,
    TEXT_WITH_DOLLAR_MATH_RE,
    split_dollar_math_in_text,
)
from arxiv2md_beta.ir.builders._shared import BIB_REF_RE
from arxiv2md_beta.ir.builders._table_spans import (
    MAX_COLSPAN,
    MAX_ROWSPAN,
    RawRow,
    clamp_span,
    expand_table_spans,
)
from arxiv2md_beta.ir.inlines import InlineUnion, TextIR
from arxiv2md_beta.utils.html_attrs import attr_str
from arxiv2md_beta.utils.html_attrs import classes as css_classes

_EQUATION_TABLE_RE = re.compile(r"ltx_equationgroup|ltx_eqn_align|ltx_eqn_table|ltx_equation")
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


# LaTeXML degrades TikZ-based math (e.g. ar5iv "\mathhl" colored highlight
# boxes around a symbol) into picture-box code:
#   \hbox to12.15pt{\vbox to7.28pt{\pgfpicture\makeatletter...\endpgfpicture}}
# The box's inner symbol is replaced with \pgfsys@hbox{<id>}, so the
# annotation alone is not math. The symbol is recoverable from the sibling
# <svg> foreignobject MathML (see _svg_foreignobject_latex). These patterns
# identify the fragments; every such fragment ends with "\endpgfpicture}"
# plus the closing braces of the \vbox/\hbox groups.
_PICTURE_HBOX_RE = re.compile(r"\\hbox\s+to\s*[\d.]*\s*pt\s*\{.*?\\endpgfpicture\}\}", re.DOTALL)
_PICTURE_BARE_RE = re.compile(r"\\pgfpicture.*?\\endpgfpicture\}\}", re.DOTALL)


def _svg_foreignobject_latex(svg_tag: Tag) -> str:
    r"""Clean LaTeX for a LaTeXML picture ``<svg>``'s boxed symbol.

    LaTeXML renders TikZ math (``\mathhl`` colored boxes) as an ``<svg
    class="ltx_picture ltx_markedasmath">`` whose ``<foreignobject>`` holds the
    boxed symbol as MathML. Prefer a nested ``<math alttext=...>`` (carries the
    original LaTeX); otherwise convert the small MathML to LaTeX.
    """
    fo = svg_tag.find("foreignobject")
    if not isinstance(fo, Tag):
        return ""
    math = fo.find("math", attrs={"alttext": True})
    if isinstance(math, Tag):
        return _normalize_math_latex(str(math["alttext"]))
    content = fo.find("span", class_="ltx_foreignobject_content") or fo
    return _mathml_node_latex(content)


def _mathml_node_latex(el: Any) -> str:
    """Convert a small MathML fragment (boxed symbol) to LaTeX.

    Handles the subset ar5iv emits for inline math rendered as SVG pictures:
    ``<msub>``/``<msubsup>``, ``<mi>`` (with bold-italic variants), ``<mo>``,
    ``<mn>``, ``<mtext>``, ``<mrow>`` and container elements. Anything it
    cannot map degrades to its text content.
    """
    if not isinstance(el, Tag):
        return str(el)
    name = el.name
    if name in ("math", "semantics", "mrow", "mstyle", "span"):
        return "".join(_mathml_node_latex(c) for c in el.contents if isinstance(c, Tag))
    if name == "msub":
        children = [c for c in el.contents if isinstance(c, Tag)]
        if len(children) == 2:
            return f"{_mathml_node_latex(children[0])}_{{{_mathml_node_latex(children[1])}}}"
        return ""
    if name == "msubsup":
        children = [c for c in el.contents if isinstance(c, Tag)]
        if len(children) == 3:
            return (
                f"{_mathml_node_latex(children[0])}"
                f"_{{{_mathml_node_latex(children[1])}}}"
                f"^{{{_mathml_node_latex(children[2])}}}"
            )
        return ""
    if name == "mi":
        text = el.get_text()
        base, bold = _MATHML_GLYPH_MAP.get(text, (text, False))
        if "bold" in (el.get("mathvariant") or "") or bold:
            base = f"\\bm{{{base}}}"
        return base
    if name in ("mo", "mn", "mtext"):
        return el.get_text()
    if name in ("annotation", "annotation-xml"):
        return ""
    return "".join(_mathml_node_latex(c) for c in el.contents if isinstance(c, Tag))


# Unicode math glyphs LaTeXML emits for \bm{...} (pre-bolded script letters)
# and plain Greek; maps to (LaTeX base, is_bold).
_MATHML_GLYPH_MAP: dict[str, tuple[str, bool]] = {
    "𝒙": ("x", True),
    "𝒗": ("v", True),
    "𝒛": ("z", True),
    "𝒆": ("e", True),
    "𝒘": ("w", True),
    "𝒚": ("y", True),
    "𝒖": ("u", True),
    "𝒕": ("t", True),
    "𝒊": ("i", True),
    "𝒋": ("j", True),
    "𝒌": ("k", True),
    "𝒑": ("p", True),
    "𝒒": ("q", True),
    "𝒓": ("r", True),
    "𝒂": ("a", True),
    "𝒃": ("b", True),
    "𝒄": ("c", True),
    "𝒅": ("d", True),
    "𝒇": ("f", True),
    "𝒈": ("g", True),
    "𝒉": ("h", True),
    "𝒔": ("s", True),
    "ϵ": ("\\epsilon", False),
    "ε": ("\\varepsilon", False),
    "θ": ("\\theta", False),
    "α": ("\\alpha", False),
    "β": ("\\beta", False),
    "γ": ("\\gamma", False),
    "δ": ("\\delta", False),
    "ζ": ("\\zeta", False),
    "η": ("\\eta", False),
    "ι": ("\\iota", False),
    "κ": ("\\kappa", False),
    "λ": ("\\lambda", False),
    "μ": ("\\mu", False),
    "ν": ("\\nu", False),
    "ξ": ("\\xi", False),
    "π": ("\\pi", False),
    "ρ": ("\\rho", False),
    "σ": ("\\sigma", False),
    "τ": ("\\tau", False),
    "φ": ("\\varphi", False),
    "χ": ("\\chi", False),
    "ψ": ("\\psi", False),
    "ω": ("\\omega", False),
}


def _substitute_picture_boxes(latex: str, symbols: list[str]) -> str:
    r"""Replace each ``\hbox to...pt{...\endpgfpicture}`` fragment with the matching recovered symbol.

    The svg picture elements and the ``\hbox`` fragments appear in the same
    order, so substitution is position-based.
    """
    it = iter(symbols)

    def _repl(_m: re.Match) -> str:
        return next(it, "")

    return _PICTURE_HBOX_RE.sub(_repl, latex)


def _extract_math_latex(tag: Tag) -> str:
    """Extract LaTeX from a <math> tag, normalizing whitespace.

    When the annotation contains LaTeXML picture-box code (a degraded TikZ
    highlight), substitute the boxed symbol recovered from the sibling
    ``<svg>`` foreignobject MathML so the formula keeps its meaning.
    """
    annotation = tag.find("annotation", attrs={"encoding": "application/x-tex"})
    latex = annotation.text.strip() if annotation and annotation.text else tag.get_text(" ", strip=True)
    if _PICTURE_HBOX_RE.search(latex):
        symbols = [_svg_foreignobject_latex(s) for s in tag.find_all("svg")]
        latex = _substitute_picture_boxes(latex, symbols)
    latex = _normalize_math_latex(latex)
    # Drop any surviving picture fragment (no recoverable symbol) — not math.
    latex = _PICTURE_HBOX_RE.sub(" ", latex)
    latex = _PICTURE_BARE_RE.sub(" ", latex)
    return latex


def _normalize_math_latex(latex: str) -> str:
    r"""Normalize math LaTeX for Markdown display.

    Literal newlines inside math (common inside ``\\mbox{...}``) break
    Markdown math rendering; collapse them to spaces and trim surrounding
    whitespace while preserving ``\\\\`` line-break commands.
    """
    # Strip ar5iv/MathJax artifacts from the <annotation encoding="application/x-tex">:
    # colored-token macros (pgfstroke / xcolor), which serialize as
    #   \color[rgb]{...}\definecolor[named]{pgfstrokecolor}{rgb}{...}
    #   \pgfsys@color@rgb@stroke{...}\pgfsys@color@rgb@fill{...}
    latex = re.sub(r"\\definecolor(?:\[[^\]]*\])?\{[^}]*\}\{[^}]*\}\{[^}]*\}", " ", latex)
    latex = re.sub(r"\\color(?:\[[^\]]*\])?\{[^}]*\}", " ", latex)
    latex = re.sub(r"\\pgfsys@color@[a-z@]+\{[^}]*\}\{[^}]*\}\{[^}]*\}", " ", latex)
    # Bare \displaystyle is redundant in $$ ... $$ display math.
    latex = re.sub(r"\\displaystyle\b", " ", latex)
    # MathJax line-wrap markers: a trailing '%' continues a wrapped line and
    # leaves stray fragments like '{% \bf Z}' or '% }\text{or}'.
    latex = re.sub(r"(?<!\\)%", " ", latex)
    # Replace literal newlines/tabs with spaces, then collapse runs of spaces.
    latex = re.sub(r"[\n\r\t]+", " ", latex)
    latex = re.sub(r" {2,}", " ", latex)
    # Tidy brace-space artifacts left by '%' removal, e.g. '{ \bf Z}'.
    # Only strip when the space precedes a backslash command — a plain
    # leading space inside '\text{ ... }' / '\mbox{ ... }' is a word
    # separator and must survive (e.g. '\gamma_{k}=0\text{ if }').
    latex = re.sub(r"\{\s+(?=\\)", "{", latex)

    # ── macro translation ────────────────────────────────────────────────
    # ar5iv annotations keep TeX-only macros that break Markdown math renderers.

    # MathJax/ar5iv serializes \begin{array} (and other environments) as
    # '\begin{array}[] {cl}' — an empty optional position argument that KaTeX
    # rejects ("Unknown column alignment"). Only EMPTY '[]' is dropped; a real
    # positional arg like '\begin{array}[t]' is valid LaTeX and is kept.
    latex = re.sub(r"\\begin\{([a-zA-Z*]+)\}\s*\[\s*\]\s*", r"\\begin{\1}", latex)

    # Independence symbol: \mbox{${}\perp\mkern-11.0mu\perp{}$} -> \perp \!\!\! \perp.
    # \mkern is math-mode-only but sits inside the text-mode \mbox, so the box
    # never renders. Run this BEFORE the generic \mbox->\text rule below: the
    # symbol's inner '{}' groups would defeat the single-level brace regex.
    # The optional '$' covers the pre-simplify annotation ('${}' / '{}') — the
    # real ar5iv form has both a leading and a trailing '$' inside the box.
    latex = PERP_IN_MATH_RE.sub(PERP_REPLACEMENT, latex)
    # The replacement ends with a space so a symbol glued to its successor
    # (e.g. "...\perp{}X^e") still reads as "...\perp X^e"; collapse any
    # double space where the source already had one.
    latex = re.sub(r" {2,}", " ", latex)
    # \mbox{text} -> \text{text} (text-mode \mbox breaks math renderers).
    latex = MBOX_TO_TEXT_RE.sub(r"\\text\1{\2}", latex)

    # KaTeX rejects math macros directly inside \text{...} (e.g. the annotation
    # '\mbox{ can be rejected at level $\alpha$}' becomes '\text{...\alpha...}'
    # after the above rule and the later '$'-stripping). Split any '$...$'
    # sub-expressions in text groups back into math mode:
    #   \text{ can be rejected at level $\alpha$}  ->  \text{ can be rejected at level } \alpha
    # (Distinct from the LaTeX builder's macro-based splitter — see
    # ``_math_norm`` for why the two implementations differ.)
    latex = TEXT_WITH_DOLLAR_MATH_RE.sub(split_dollar_math_in_text, latex)
    # TeX line-break hints (\nolinebreak) are unsupported by some renderers
    # and visually no-ops in display math; drop them.
    latex = NOLINEBREAK_RE.sub("", latex)
    return latex.strip()


# ar5iv placeholder alt texts that carry no information; emit empty alt instead.
_PLACEHOLDER_IMAGE_ALTS = frozenset(
    {"", "[uncaptioned image]", "uncaptioned image", "refer to caption", "[ refer to caption ]"}
)


def _graphic_src(tag: Tag) -> str:
    """Return the image URL of an ``<img>``/``<object>`` graphic.

    LaTeXML renders vector figures as ``<object type="image/svg+xml"
    data="...">``; the URL lives in ``data`` rather than ``src``.
    """
    return attr_str(tag, "data") if tag.name == "object" else attr_str(tag, "src")


# algorithmic-package keywords that prefix a pseudocode line but carry no
# readable content themselves ("\\State" is a pure line break).
_ALGO_MARKER_KEYWORDS = {"state": ""}


def _algorithm_keyword(raw: str) -> str:
    r"""Map an unknown-command marker (``\EndWhile``) to readable pseudocode text."""
    word = raw.strip().lstrip("\\").strip()
    if not word:
        return ""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", word).lower()
    return _ALGO_MARKER_KEYWORDS.get(spaced, spaced)


def _get_marker_text(tag: Tag) -> str:
    marker_text = tag.get_text(strip=True)
    return marker_text if isinstance(marker_text, str) else ""


def _split_at_breaks(inlines: list) -> list[list]:
    """Split an inline sequence at BreakIR boundaries into per-line groups."""
    groups: list[list] = []
    current: list = []
    for inline in inlines:
        if getattr(inline, "type", "") == "break":
            if current:
                groups.append(current)
                current = []
        else:
            current.append(inline)
    if current:
        groups.append(current)
    return groups or [[]]


def _trim_inline_edges(inlines: list) -> list:
    r"""Drop edge whitespace and trim the outermost text inlines of a line.

    Pseudocode lines are emitted verbatim, so a listing row's incidental
    indentation ("<div>\n  Input: ...") must not leak into the output.
    """
    while inlines and isinstance(inlines[0], TextIR) and not inlines[0].text.strip():
        inlines = inlines[1:]
    while inlines and isinstance(inlines[-1], TextIR) and not inlines[-1].text.strip():
        inlines = inlines[:-1]
    if not inlines:
        return inlines
    if isinstance(inlines[0], TextIR):
        inlines[0] = inlines[0].model_copy(update={"text": inlines[0].text.lstrip()})
    if isinstance(inlines[-1], TextIR):
        inlines[-1] = inlines[-1].model_copy(update={"text": inlines[-1].text.rstrip()})
    return [il for il in inlines if not (il.type == "text" and il.text == "")]


# Pseudocode keywords that open a nesting level ("for all ..." counts: the
# first word decides). Closers must be checked before openers ("end for").
_ALGO_LIST_OPENERS = frozenset({"while", "for", "if", "loop", "function"})
_ALGO_LIST_CLOSERS = ("end while", "end for", "end if", "end loop", "end function")


def _step_list_kind(step: BlockUnion) -> str:
    """Classify a pseudocode line as ``opener``/``closer``/``plain`` for nesting."""
    if getattr(step, "type", "") != "paragraph":
        return "plain"
    for il in getattr(step, "inlines", []):
        if getattr(il, "type", "") != "text":
            continue
        text = il.text.strip().lower()
        if not text:
            continue
        if text.startswith(_ALGO_LIST_CLOSERS):
            return "closer"
        if text.split(maxsplit=1)[0] in _ALGO_LIST_OPENERS:
            return "opener"
        return "plain"
    return "plain"


def _nest_algorithm_steps(steps: list[BlockUnion]) -> list[BlockUnion]:
    """Group flat pseudocode lines into a bullet list nested under while/for/if.

    Each line becomes one list item; lines following an opener are indented one
    level deeper until the matching ``end ...`` line, which sits at the opener's
    own level — mirroring the visual indentation of the rendered algorithm.
    """
    depth = 0
    depths: list[int] = []
    for step in steps:
        kind = _step_list_kind(step)
        if kind == "closer":
            depth = max(0, depth - 1)
            depths.append(depth)
        elif kind == "opener":
            depths.append(depth)
            depth += 1
        else:
            depths.append(depth)

    # nodes are [block, child_nodes] pairs; the stack holds the open levels.
    root: list = []
    stack = [root]
    for step, level in zip(steps, depths, strict=True):
        while len(stack) > level + 1:
            stack.pop()
        while len(stack) < level + 1:
            current = stack[-1]
            if not current:  # depth jump without an opener line — clamp
                break
            stack.append(current[-1][1])
        stack[-1].append([step, []])

    def to_items(nodes: list) -> list[list[BlockUnion]]:
        items: list[list[BlockUnion]] = []
        for blk, children in nodes:
            item: list[BlockUnion] = [blk]
            if children:
                item.append(ListIR(items=to_items(children), ordered=False))
            items.append(item)
        return items

    return [ListIR(items=to_items(root), ordered=False)]


def _clean_image_alt(alt: str) -> str:
    """Sanitize an ``<img>`` alt string for Markdown ``![alt](src)`` syntax.

    ar5iv uses placeholder alts (``[Uncaptioned image]``, ``Refer to caption``)
    that contain square brackets and would break Markdown image syntax
    (``![[Uncaptioned image]](src)`` parses as ``!`` + a wikilink). Replace
    placeholders with empty alt and strip ``[``/``]`` from any real alt.
    """
    if not alt:
        return ""
    stripped = alt.strip()
    if stripped.lower() in _PLACEHOLDER_IMAGE_ALTS:
        return ""
    return stripped.replace("[", "").replace("]", "").strip()


def _is_citation_link(href: str) -> bool:
    if not href:
        return False
    return bool(BIB_REF_RE.search(href))


def _extract_citation_ref(href: str) -> str | None:
    m = BIB_REF_RE.search(href)
    if m:
        return f"ref-{m.group(1)}"
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

    Spans are materialized first (audit4 P2 colspan, audit5 G1-5 rowspan): a
    cell spanning N columns is repeated N times, and a cell spanning N rows
    leaves empty placeholders in those rows, so pipe-table columns stay
    aligned.

    Returns:
        headers: One cell per header column, each cell = list[InlineUnion].
        rows: Each row = list of cells, each cell = list[InlineUnion].
    """
    raw_rows: list[RawRow] = []
    first_row_is_header = False

    def collect_rows(container: Tag) -> int:
        """Append raw span-aware cells of every direct <tr>; return count."""
        added = 0
        for row in container.find_all("tr", recursive=False):
            raw_cells: RawRow = [
                (
                    tag_to_inlines(cell),
                    clamp_span(cell.get("colspan"), MAX_COLSPAN),
                    clamp_span(cell.get("rowspan"), MAX_ROWSPAN),
                )
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if raw_cells:
                raw_rows.append(raw_cells)
                added += 1
        return added

    for section in table.find_all(["thead", "tbody", "tfoot"], recursive=False):
        before = len(raw_rows)
        collect_rows(section)
        if section.name == "thead" and before == 0 and len(raw_rows) > before:
            first_row_is_header = True

    # Fallback: no thead/tbody — use first row as header
    if not raw_rows and collect_rows(table):
        first_row_is_header = True

    grid = expand_table_spans(raw_rows)
    if first_row_is_header and grid:
        return grid[0], grid[1:]
    return [], grid


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
