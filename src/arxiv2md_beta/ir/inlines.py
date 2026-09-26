"""Inline IR node types — text-level elements.

All inline nodes inherit from ``InlineIR`` and use a ``type`` literal discriminator
so Pydantic can (de)serialise heterogeneous lists of inlines.

Union type
    ``InlineUnion`` is the discriminated union of all inline node types and should
    be used in any field that accepts a heterogeneous list of inlines:

    .. code-block:: python

        from arxiv2md_beta.ir.inlines import InlineUnion

        class ParagraphIR(BlockIR):
            inlines: list[InlineUnion]
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from arxiv2md_beta.ir.core import InlineIR

# ═══════════════════════════════════════════════════════════════════════
# Leaf nodes
# ═══════════════════════════════════════════════════════════════════════


class TextIR(InlineIR):
    """Plain text."""

    type: Literal["text"] = "text"
    text: str


class MathIR(InlineIR):
    """Inline or display math (LaTeX source)."""

    type: Literal["math"] = "math"
    latex: str
    display: bool = False  # True → display math block; False → inline $...$


class ImageRefIR(InlineIR):
    """Reference to an image asset (used inline, e.g. inside a FigureIR)."""

    type: Literal["image_ref"] = "image_ref"
    src: str
    alt: str = ""


class BreakIR(InlineIR):
    """Hard line-break (``<br/>``)."""

    type: Literal["break"] = "break"


class RawInlineIR(InlineIR):
    """Fallback: raw inline content whose format we could not parse.

    Preserves the original source so no information is lost.
    """

    type: Literal["raw_inline"] = "raw_inline"
    format: Literal["html", "latex", "markdown"] = "html"
    content: str


# ═══════════════════════════════════════════════════════════════════════
# Container nodes (contain other InlineIRs)
# ═══════════════════════════════════════════════════════════════════════


class EmphasisIR(InlineIR):
    """Styled text span: italic, bold, code, underline, strikethrough, smallcaps.

    ``smallcaps`` has no native Markdown delimiter and is emitted as plain text
    by the MarkdownEmitter (delimiters default to empty).
    """

    type: Literal["emphasis"] = "emphasis"
    style: Literal["italic", "bold", "code", "underline", "strikethrough", "smallcaps"] = "italic"
    inlines: list[InlineUnion] = Field(default_factory=list)


class LinkIR(InlineIR):
    """Hyperlink — unified model for external URLs, internal anchors, etc.

    Also covers citation references and footnote references.

    The ``kind`` discriminator tells the emitter how to render the link.
    ``target_id`` is set by the *ResolveRefsPass* after cross-reference
    resolution.
    """

    type: Literal["link"] = "link"
    url: str | None = None
    inlines: list[InlineUnion] = Field(default_factory=list)
    kind: Literal["external", "internal", "citation", "footnote"] = "external"
    target_id: str | None = None


class SuperscriptIR(InlineIR):
    """Superscript text span."""

    type: Literal["superscript"] = "superscript"
    inlines: list[InlineUnion] = Field(default_factory=list)


class SubscriptIR(InlineIR):
    """Subscript text span."""

    type: Literal["subscript"] = "subscript"
    inlines: list[InlineUnion] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Discriminated union
# ═══════════════════════════════════════════════════════════════════════

InlineUnion = Annotated[
    TextIR | EmphasisIR | LinkIR | MathIR | ImageRefIR | SuperscriptIR | SubscriptIR | BreakIR | RawInlineIR,
    Field(discriminator="type"),
]


# ═══════════════════════════════════════════════════════════════════════
# Inline-list flattening (single shared implementation)
# ═══════════════════════════════════════════════════════════════════════


def inlines_to_plain_text(
    inlines: list[InlineUnion],
    *,
    math: Literal["dollar", "raw"] = "raw",
    image: Literal["alt", "fallback"] = "alt",
    raw: Literal["content", "skip"] = "content",
    include_link_url: bool = False,
    joiner: str = "",
) -> str:
    """Flatten a list of inline nodes to plain text.

    Single source of truth for the "IR inlines → pattern-matchable string"
    walks that previously existed as near-copies in the LaTeX builder
    (``LaTeXBuilder._inlines_to_plain_text``) and the figure-reorder pass
    (``figure_reorder._inlines_to_text``). The two consumers genuinely need
    different renderings, so the knob is explicit rather than duplicated:

    - ``math``: ``$...$`` / ``$$...$$`` wrapping (builder, preview-style) or
      the bare ``latex`` source (figure-citation matching).
    - ``image``: image ``alt`` only, or ``alt`` with a ``"[image]"`` fallback
      when the alt is empty (builder).
    - ``raw``: include ``RawInlineIR.content`` (figure matching) or skip raw
      inlines (builder).
    - ``include_link_url``: prepend the link URL before the link text
      (figure matching needs URLs visible; the builder only wants labels).
    - ``joiner``: ``""`` (builder concatenates) or ``" "`` (figure matching
      keeps word boundaries).

    ``visitor.TextCollector`` intentionally stays a separate mechanism: it
    walks whole trees and returns *unjoined* per-node strings for token
    counting, not a single flattened string.
    """
    parts: list[str] = []
    for il in inlines:
        if isinstance(il, TextIR):
            parts.append(il.text)
        elif isinstance(il, MathIR):
            if math == "dollar":
                parts.append(f"${il.latex}$" if not il.display else f"$${il.latex}$$")
            else:
                parts.append(il.latex)
        elif isinstance(il, ImageRefIR):
            if image == "fallback":
                parts.append(il.alt or "[image]")
            else:
                parts.append(il.alt)
        elif isinstance(il, RawInlineIR):
            if raw == "content":
                parts.append(il.content)
            # else: skip raw inline
        elif isinstance(il, LinkIR):
            if include_link_url and il.url:
                parts.append(il.url)
            parts.append(
                inlines_to_plain_text(
                    il.inlines,
                    math=math,
                    image=image,
                    raw=raw,
                    include_link_url=include_link_url,
                    joiner=joiner,
                )
            )
        elif isinstance(il, EmphasisIR | SuperscriptIR | SubscriptIR):
            parts.append(
                inlines_to_plain_text(
                    il.inlines,
                    math=math,
                    image=image,
                    raw=raw,
                    include_link_url=include_link_url,
                    joiner=joiner,
                )
            )
        # BreakIR has no text content; skip.
    return joiner.join(parts)
