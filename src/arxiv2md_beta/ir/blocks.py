"""Block-level IR node types.

Each block type is a subclass of ``BlockIR`` with a ``type`` literal discriminator
that identifies the block kind.

Union type
    ``BlockUnion`` is the discriminated union of all block types:

    .. code-block:: python

        from arxiv2md_beta.ir.blocks import BlockUnion

        class SectionIR(IRNode):
            blocks: list[BlockUnion] = Field(default_factory=list)
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from arxiv2md_beta.ir.core import BlockIR
from arxiv2md_beta.ir.inlines import ImageRefIR, InlineUnion

# ═══════════════════════════════════════════════════════════════════════
# Basic blocks
# ═══════════════════════════════════════════════════════════════════════


class ParagraphIR(BlockIR):
    """A paragraph — a sequence of inline elements."""

    type: Literal["paragraph"] = "paragraph"
    inlines: list[InlineUnion] = Field(default_factory=list)


class HeadingIR(BlockIR):
    """A heading inside a section body (not the section title itself)."""

    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    inlines: list[InlineUnion] = Field(default_factory=list)


class BlockQuoteIR(BlockIR):
    """A blockquote containing nested blocks."""

    type: Literal["blockquote"] = "blockquote"
    blocks: list["BlockUnion"] = Field(default_factory=list)


class ListIR(BlockIR):
    """Ordered or unordered list.

    Each item is a sequence of blocks (supporting nested paragraphs, sub-lists, code, etc.).
    """

    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[list["BlockUnion"]] = Field(default_factory=list)


class CodeIR(BlockIR):
    """Fenced code block with optional language and caption."""

    type: Literal["code"] = "code"
    language: str | None = None
    text: str
    caption: list[InlineUnion] | None = None


class RuleIR(BlockIR):
    """Horizontal rule (``<hr/>`` or ``\\hrule``)."""

    type: Literal["rule"] = "rule"


# ═══════════════════════════════════════════════════════════════════════
# Math & display content
# ═══════════════════════════════════════════════════════════════════════


class EquationIR(BlockIR):
    """Display-math equation (``$$...$$``, ``\\[...\\]``, equation environment)."""

    type: Literal["equation"] = "equation"
    latex: str
    equation_number: str | None = None  # e.g. "(1)", "1.2"


# ═══════════════════════════════════════════════════════════════════════
# Figures, tables & algorithms
# ═══════════════════════════════════════════════════════════════════════


class FigureIR(BlockIR):
    """A figure with one or more images and a caption.

    ``kind`` distinguishes image figures from table-figures and algorithm-figures
    (common in HTML-formatted arXiv papers).
    """

    type: Literal["figure"] = "figure"
    images: list[ImageRefIR] = Field(default_factory=list)
    caption: list[InlineUnion] = Field(default_factory=list)
    figure_id: str | None = None  # e.g. "figure-2"
    kind: Literal["image", "table", "algorithm"] = "image"
    width: str | None = None


class TableIR(BlockIR):
    """A structured table with headers, rows, and an optional caption."""

    type: Literal["table"] = "table"
    headers: list[list[InlineUnion]] = Field(default_factory=list)
    rows: list[list[list[InlineUnion]]] = Field(default_factory=list)
    caption: list[InlineUnion] = Field(default_factory=list)
    table_id: str | None = None  # e.g. "table-1"


class AlgorithmIR(BlockIR):
    """An algorithm or pseudocode block."""

    type: Literal["algorithm"] = "algorithm"
    steps: list["BlockUnion"] = Field(default_factory=list)
    caption: list[InlineUnion] = Field(default_factory=list)
    algorithm_number: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# Fallback
# ═══════════════════════════════════════════════════════════════════════


class RawBlockIR(BlockIR):
    """Fallback: raw block whose format we could not classify.

    Preserves the original source content so no information is lost.
    """

    type: Literal["raw_block"] = "raw_block"
    format: Literal["html", "latex", "markdown"] = "html"
    content: str


# ═══════════════════════════════════════════════════════════════════════
# Discriminated union
# ═══════════════════════════════════════════════════════════════════════

BlockUnion = Annotated[
    Union[
        ParagraphIR,
        HeadingIR,
        FigureIR,
        TableIR,
        ListIR,
        CodeIR,
        EquationIR,
        BlockQuoteIR,
        AlgorithmIR,
        RuleIR,
        RawBlockIR,
    ],
    Field(discriminator="type"),
]
