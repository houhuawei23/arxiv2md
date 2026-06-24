"""Final Markdown post-processing: optional anchors, math cleaning, inline spacing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arxiv2md_beta.schemas import IngestionResult

from arxiv2md_beta.settings import get_settings

_ANCHOR_TAG_RE = re.compile(r'<a id="[^"]*"></a>')
_TRAILING_MATH_SPACE_RE = re.compile(r"\\(?: |\,|\;|\:|\!|quad|qquad|hspace\{[^}]*\})\s*$")
# Multi-line display math blocks, capturing leading indentation on both fences.
_DISPLAY_MATH_BLOCK_RE = re.compile(
    r"^([ \t]*)\$\$\n(.*?)\n\1\$\$",
    re.DOTALL | re.MULTILINE,
)


def _remove_anchor_tags(text: str) -> str:
    r"""Strip all ``<a id=\"...\"></a>`` anchors and normalize leftover blank lines."""
    text = _ANCHOR_TAG_RE.sub("", text)
    # Collapse 3+ newlines to 2 and trim trailing whitespace per line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


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

    # Step 2: tokenize remaining text for inline math and single-line display math.
    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    buf: list[str] = []

    def flush() -> None:
        if buf:
            tokens.append(("text", "".join(buf)))
            buf.clear()

    while i < n:
        if text.startswith("$$", i):
            flush()
            j = text.find("$$", i + 2)
            if j == -1:
                buf.append(text[i:])
                break
            tokens.append(("display", text[i + 2 : j]))
            i = j + 2
        elif text[i] == "$":
            flush()
            j = text.find("$", i + 1)
            if j == -1 or "\n" in text[i + 1 : j]:
                buf.append("$")
                i += 1
            else:
                tokens.append(("inline", text[i + 1 : j]))
                i = j + 1
        else:
            buf.append(text[i])
            i += 1
    flush()

    out_parts: list[str] = []
    for idx, (kind, val) in enumerate(tokens):
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
        nxt = tokens[idx + 1][1][0] if idx + 1 < len(tokens) else ""
        s = f"${cleaned}$"
        if prev and prev.isalnum():
            s = " " + s
        if nxt and nxt.isalnum():
            s = s + " "
        out_parts.append(s)

    result = "".join(out_parts)

    # Step 3: restore protected display math blocks.
    for idx, replacement in enumerate(protected):
        result = result.replace(f"\x00DISPLAY_MATH_{idx}\x00", replacement)

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
    if not include_anchors:
        text = _remove_anchor_tags(text)
    text = _clean_math_and_spacing(text)
    # Ensure no excessive blank lines remain.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def apply_markdown_postprocessing(
    result: IngestionResult,
    *,
    include_anchors: bool | None = None,
) -> IngestionResult:
    """Return a new :class:`IngestionResult` with final Markdown cleanup applied."""
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
