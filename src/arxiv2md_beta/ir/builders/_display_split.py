r"""Split paragraph inlines at display-math inlines — shared by both builders.

Pandoc places display math (``\begin{align}``, ``$$…$$``) INSIDE a Para as a
``DisplayMath`` inline, possibly nested inside a container inline (an
emphasis wrapping an ``assumption`` environment, …); ar5iv occasionally wraps
display equations in paragraph wrappers too. Both builders lift such math to
block-level :class:`EquationIR` so the MarkdownEmitter renders it as a
standalone ``$$…$$`` block — mid-paragraph ``$$`` is absorbed into the
surrounding text and breaks KaTeX/MathJax.

This used to be two drifting implementations (``HTMLBuilder._split_paragraph_inlines``
without container recursion, ``LaTeXBuilder._split_display_math_paragraph``
with it); the shared version takes the stronger recursive semantics for both
paths and drops whitespace-only text runs.
"""

from __future__ import annotations

import copy
from typing import cast

from arxiv2md_beta.ir.blocks import BlockUnion, EquationIR, ParagraphIR
from arxiv2md_beta.ir.inlines import InlineUnion, MathIR


def _contains_display_math(inlines: list[InlineUnion]) -> bool:
    """True if any inline (recursively, through container inlines) is display math."""
    for il in inlines:
        if isinstance(il, MathIR) and il.display:
            return True
        if hasattr(il, "inlines") and _contains_display_math(il.inlines):
            return True
    return False


def _partition_display_math(inlines: list[InlineUnion]) -> list[tuple[str, list[InlineUnion] | str]]:
    r"""Partition *inlines* into ``("text", inlines)`` / ``("eq", latex)`` segments.

    Display math inside container inlines is lifted out; the surrounding text
    is re-wrapped in a clone of the container so styling is preserved.
    """
    segments: list[tuple[str, list[InlineUnion] | str]] = []
    current: list[InlineUnion] = []

    def flush() -> None:
        if current:
            segments.append(("text", list(current)))
            current.clear()

    for il in inlines:
        if isinstance(il, MathIR) and il.display:
            flush()
            segments.append(("eq", il.latex))
        elif hasattr(il, "inlines") and _contains_display_math(il.inlines):
            sub = _partition_display_math(il.inlines)
            for kind, val in sub:
                if kind == "eq":
                    flush()
                    segments.append(("eq", val))
                else:
                    cloned = copy.deepcopy(il)
                    cloned.inlines = list(val)  # type: ignore[attr-defined]
                    current.append(cloned)
        else:
            current.append(il)
    flush()
    return segments


def has_content(inlines: list[InlineUnion]) -> bool:
    """False when every inline is whitespace-only text (nothing to render)."""
    return any(not (il.type == "text" and not il.text.strip()) for il in inlines)


def split_paragraph_at_display_math(inlines: list[InlineUnion]) -> list[BlockUnion]:
    """Split paragraph inlines into paragraph/equation blocks.

    Returns a single-element list with one :class:`ParagraphIR` when no
    display math is present, and ``[]`` for whitespace-only input.
    """
    if not has_content(inlines):
        return []
    if not _contains_display_math(inlines):
        return [ParagraphIR(inlines=inlines)]
    blocks: list[BlockUnion] = []
    for kind, val in _partition_display_math(inlines):
        if kind == "eq":
            blocks.append(EquationIR(latex=cast("str", val)))
        elif has_content(cast("list[InlineUnion]", val)):
            blocks.append(ParagraphIR(inlines=list(cast("list[InlineUnion]", val))))
    return blocks
