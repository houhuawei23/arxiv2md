"""Final Markdown post-processing: optional anchors, math cleaning, inline spacing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arxiv2md_beta.schemas import IngestionResult

from arxiv2md_beta.output.markdown_utils import protect_fenced_code, restore_protected_code
from arxiv2md_beta.settings import get_settings

_ANCHOR_TAG_RE = re.compile(r'<a id="[^"]*"></a>')
_TRAILING_MATH_SPACE_RE = re.compile(r"\\(?: |\,|\;|\:|\!|quad|qquad|hspace\{[^}]*\})\s*$")
# Multi-line display math blocks, capturing leading indentation on both fences.
_DISPLAY_MATH_BLOCK_RE = re.compile(
    r"^([ \t]*)\$\$\n(.*?)\n\1\$\$",
    re.DOTALL | re.MULTILINE,
)
# Step-1 protection placeholder (\x00 sentinel + index).
_DISPLAY_MATH_PLACEHOLDER_RE = re.compile(r"\x00DISPLAY_MATH_(\d+)\x00")
# Single-line inline code spans (`` `$x$` ``): the math scanner must not pair
# the dollars inside them. Multi-line code spans don't occur in emitted docs.
_INLINE_CODE_RE = re.compile(r"`{1,3}[^`\n]+`{1,3}")
_INLINE_CODE_PLACEHOLDER_RE = re.compile(r"\x00INLINE_CODE_(\d+)\x00")
# Inline links/images: a URL containing '$' (pairing would rewrite it into a
# broken link). Alt text may carry ``\[``-escaped brackets; matching stops at
# the first unescaped-shaped ']' — links with literal ']' in alt + '$' in URL
# stay unprotected, an accepted edge.
_INLINE_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")
_INLINE_LINK_PLACEHOLDER_RE = re.compile(r"\x00MD_LINK_(\d+)\x00")
# Pipe-table rows (any line whose first non-blank char is '|'): rewriting '$'
# content inside a row can inject spacing or a multi-line display block that
# tears the table apart. Display-math blocks are already placeholders by the
# time this runs, so their content cannot trigger a false row match.
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*$", re.MULTILINE)
_TABLE_ROW_PLACEHOLDER_RE = re.compile(r"\x00MD_ROW_(\d+)\x00")


def _strip_anchor_tags(text: str) -> str:
    r"""Strip all ``<a id=\"...\"></a>`` anchors and normalize leftover blank lines.

    Caller has already lifted fenced code blocks out: blank-line collapsing
    and per-line ``rstrip`` must not touch their contents.
    """
    text = _ANCHOR_TAG_RE.sub("", text)
    # Collapse 3+ newlines to 2 and trim trailing whitespace per line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _clean_math_latex(latex: str) -> str:
    """Trim whitespace and remove trailing LaTeX spacing commands from math."""
    while True:
        new = _TRAILING_MATH_SPACE_RE.sub("", latex)
        new = new.rstrip()
        if new == latex:
            break
        latex = new
    return latex.strip()


def _clean_math_and_spacing(text: str) -> str:
    """Clean math latex and ensure inline math has spaces around ``$`` delimiters.

    Multi-line display math blocks preserve their original indentation so that
    equations remain valid when nested inside list items.

    The scanner tokenizes on single ``$`` characters (``re.split``) instead of
    walking the string character by character — same semantics, ~50x faster on
    megabyte documents. Two consecutive ``$`` tokens are a display-math
    delimiter pair opener; a lone ``$`` opens inline math. Inline candidates
    follow pandoc's flanking rule: a ``$`` pair whose content starts or ends
    with whitespace is literal text (so prose like ``price $5 and $10`` is
    left alone). A lone ``$$`` anywhere makes the rest of its segment literal
    — accepted degradation for pathological input.

    Fenced code blocks and inline code spans are lifted out first: neither the
    display-block pass nor the ``$`` scanner may rewrite their contents. Inline
    links/images and pipe-table rows are likewise protected — a URL containing
    ``$`` would be rewritten into a broken link, and touching ``$`` content
    inside a row can tear the table apart.

    Fenced code blocks must already be lifted by the caller (one lift per
    finalize; audit5 X6).
    """
    # Step 1: protect multi-line display math blocks and preserve indentation.
    protected: list[str] = []

    def _protect_display(m: re.Match) -> str:
        indent = m.group(1)
        cleaned = _clean_math_latex(m.group(2))
        replacement = f"{indent}$$\n{indent}{cleaned}\n{indent}$$"
        protected.append(replacement)
        return f"\x00DISPLAY_MATH_{len(protected) - 1}\x00"

    text = _DISPLAY_MATH_BLOCK_RE.sub(_protect_display, text)

    # Step 1b: protect inline code spans (`` `$x$` ``) from the dollar scanner.
    inline_protected: list[str] = []

    def _protect_inline(m: re.Match) -> str:
        inline_protected.append(m.group(0))
        return f"\x00INLINE_CODE_{len(inline_protected) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_protect_inline, text)

    # Step 1c: protect inline links/images and pipe-table rows — the scanner
    # must never rewrite their '$' content (URLs would corrupt, tables tear).
    link_protected: list[str] = []

    def _protect_link(m: re.Match) -> str:
        link_protected.append(m.group(0))
        return f"\x00MD_LINK_{len(link_protected) - 1}\x00"

    text = _INLINE_LINK_RE.sub(_protect_link, text)

    row_protected: list[str] = []

    def _protect_row(m: re.Match) -> str:
        row_protected.append(m.group(0))
        return f"\x00MD_ROW_{len(row_protected) - 1}\x00"

    text = _TABLE_ROW_RE.sub(_protect_row, text)

    # Step 2: tokenize the remaining text on "$" and parse math regions.
    # tokens alternates literal text and "$" markers: re.split(r"(\$)", s).
    raw_tokens = re.split(r"(\$)", text)
    tokens: list[tuple[bool, str]] = [(i % 2 == 1, tok) for i, tok in enumerate(raw_tokens) if tok]
    n = len(tokens)

    def _next_dollar(start: int) -> int | None:
        """Index of the next "$" token at or after *start*."""
        for k in range(start, n):
            if tokens[k][0]:
                return k
        return None

    # Region parse — produces the same ("text"|"display"|"inline", value)
    # sequence the original character scanner produced.
    regions: list[tuple[str, str]] = []
    text_buf: list[str] = []

    def _flush_text() -> None:
        if text_buf:
            regions.append(("text", "".join(text_buf)))
            text_buf.clear()

    i = 0
    while i < n:
        is_dollar, val = tokens[i]
        if not is_dollar:
            text_buf.append(val)
            i += 1
            continue

        # Display math: two consecutive "$" tokens.
        if i + 1 < n and tokens[i + 1][0]:
            close = None
            k = i + 2
            while k < n:
                if tokens[k][0] and k + 1 < n and tokens[k + 1][0]:
                    close = k
                    break
                k += 1
            if close is None:
                # Unmatched "$$": the rest of the text is literal.
                text_buf.extend(v for _, v in tokens[i:])
                i = n
                break
            _flush_text()
            regions.append(("display", "".join(v for _, v in tokens[i + 2 : close])))
            i = close + 2
            continue

        # Inline math: the first "$" whose content has no flanking whitespace
        # closes it (pandoc's rule — ``$5 and $10`` never becomes math).
        # A candidate's outermost characters are the first/last token's
        # outermost characters, so each candidate is O(1) to accept or
        # reject — no full-window re-join per rejection (audit5 X7).
        content: str | None = None
        close = None
        k = i + 1
        while True:
            close = _next_dollar(k)
            if close is None:
                break
            if close > i + 1:
                c_first = tokens[i + 1][1][0]
                c_last = tokens[close - 1][1][-1]
                if not c_first.isspace() and not c_last.isspace():
                    content = "".join(v for _, v in tokens[i + 1 : close])
                    break
            k = close + 1
        if content is None:
            # Unmatched "$": literal, merges into the following text.
            text_buf.append("$")
            i += 1
            continue
        assert close is not None  # set whenever content was found
        _flush_text()
        if "\n" in content:
            # Inline math spanning a newline (pandoc SoftBreak inside
            # ``$...$``). A literal newline inside ``$...$`` breaks most
            # Markdown math renderers and unbalances every later ``$``
            # pair in the file — collapse it to a space and keep the math.
            content = re.sub(r"\s*\n\s*", " ", content).strip()
        regions.append(("inline", content))
        i = close + 1
    _flush_text()

    out_parts: list[str] = []
    for idx, (kind, val) in enumerate(regions):
        if kind == "text":
            out_parts.append(val)
            continue
        if kind == "display":
            cleaned = _clean_math_latex(val)
            out_parts.append(f"$$\n{cleaned}\n$$")
            continue

        # Inline math: clean and add surrounding spaces when adjacent to non-space text.
        cleaned = _clean_math_latex(val)
        prev = out_parts[-1][-1] if out_parts else ""
        # ``[:1]`` — a region value can be empty ("$$$$" inline form).
        nxt = regions[idx + 1][1][:1] if idx + 1 < len(regions) else ""
        s = f"${cleaned}$"
        if prev and prev.isalnum():
            s = " " + s
        if nxt and nxt.isalnum():
            s = s + " "
        out_parts.append(s)

    result = "".join(out_parts)

    # Step 3: restore protected spans in reverse protection order (row content
    # embeds link placeholders, link content may embed code placeholders).
    result = _TABLE_ROW_PLACEHOLDER_RE.sub(lambda m: row_protected[int(m.group(1))], result)
    result = _INLINE_LINK_PLACEHOLDER_RE.sub(lambda m: link_protected[int(m.group(1))], result)
    result = _INLINE_CODE_PLACEHOLDER_RE.sub(lambda m: inline_protected[int(m.group(1))], result)

    def _restore(m: re.Match) -> str:
        return protected[int(m.group(1))]

    result = _DISPLAY_MATH_PLACEHOLDER_RE.sub(_restore, result)
    return result


def clean_markdown_output(text: str, *, include_anchors: bool | None = None) -> str:
    r"""Apply final Markdown cleanup.

    Parameters
    ----------
    text
        Raw Markdown content.
    include_anchors
        If ``True``, keep ``<a id=\"...\"></a>`` tags. If ``None``, read from
        ``settings.output.include_anchors`` (default ``False``).

    Returns:
    -------
    str
        Cleaned Markdown content.
    """
    if include_anchors is None:
        include_anchors = get_settings().output.include_anchors
    if not text:
        return text
    # ONE fence lift for the whole cleanup (audit5 X6): the sub-passes used
    # to re-lift the already fence-free text themselves, two extra full-text
    # line scans per finalize. The final blank-line collapse below must not
    # touch fence contents either.
    text, saved_fences = protect_fenced_code(text)
    if not include_anchors:
        text = _strip_anchor_tags(text)
    text = _clean_math_and_spacing(text)
    # Ensure no excessive blank lines remain.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = restore_protected_code(text, saved_fences)
    # Emit a single trailing newline (POSIX text convention; keeps written
    # .md files and goldens stable under end-of-file-fixer).
    return text.strip() + "\n"


def apply_markdown_postprocessing(
    result: IngestionResult,
    *,
    include_anchors: bool | None = None,
) -> IngestionResult:
    """Return a new :class:`IngestionResult` with final Markdown cleanup applied.

    Deprecated: ``finalize_markdown`` now runs the full cleanup (format +
    clean) at emission time (``emit_split_markdown``), so result fields are
    already finalized when they reach the CLI layer. Retained for callers
    that emit markdown outside the shared helper.
    """
    return result.model_copy(
        update={
            "content": clean_markdown_output(result.content, include_anchors=include_anchors),
            "content_references": (
                clean_markdown_output(result.content_references, include_anchors=include_anchors)
                if result.content_references is not None
                else None
            ),
            "content_appendix": (
                clean_markdown_output(result.content_appendix, include_anchors=include_anchors)
                if result.content_appendix is not None
                else None
            ),
        }
    )


def finalize_markdown(text: str, *, include_anchors: bool | None = None) -> str:
    """Single-pass Markdown finalization: format then clean.

    Composes :func:`format_markdown_output` (anchor newlines, table captions,
    display-math simplification, bullet dedup, blank-line collapse) with
    :func:`clean_markdown_output` (optional anchor stripping, math-latex
    cleanup, inline ``$`` spacing). Replaces the former two-layer postprocess
    (one pass at emission, a second at CLI finalize) with a single application
    right after emission.
    """
    from arxiv2md_beta.output.markdown_utils import format_markdown_output

    return clean_markdown_output(format_markdown_output(text), include_anchors=include_anchors)
