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

from typing import Annotated, Literal, Union

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
    width: str | None = None
    height: str | None = None


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
    """Styled text span: italic, bold, code, underline, strikethrough."""

    type: Literal["emphasis"] = "emphasis"
    style: Literal["italic", "bold", "code", "underline", "strikethrough"] = "italic"
    inlines: list["InlineUnion"] = Field(default_factory=list)


class LinkIR(InlineIR):
    """Hyperlink — unified model for external URLs, internal anchors,
    citation references, and footnote references.

    The ``kind`` discriminator tells the emitter how to render the link.
    ``target_id`` is set by the *ResolveRefsPass* after cross-reference
    resolution.
    """

    type: Literal["link"] = "link"
    url: str | None = None
    inlines: list["InlineUnion"] = Field(default_factory=list)
    kind: Literal["external", "internal", "citation", "footnote"] = "external"
    target_id: str | None = None


class SuperscriptIR(InlineIR):
    """Superscript text span."""

    type: Literal["superscript"] = "superscript"
    inlines: list["InlineUnion"] = Field(default_factory=list)


class SubscriptIR(InlineIR):
    """Subscript text span."""

    type: Literal["subscript"] = "subscript"
    inlines: list["InlineUnion"] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Discriminated union
# ═══════════════════════════════════════════════════════════════════════

InlineUnion = Annotated[
    Union[
        TextIR,
        EmphasisIR,
        LinkIR,
        MathIR,
        ImageRefIR,
        SuperscriptIR,
        SubscriptIR,
        BreakIR,
        RawInlineIR,
    ],
    Field(discriminator="type"),
]
