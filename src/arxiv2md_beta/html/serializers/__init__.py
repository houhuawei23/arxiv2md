"""HTML to Markdown serializers.

This package contains pluggable serializers for converting HTML elements
to Markdown. Each serializer handles a specific type of HTML element.
"""

from __future__ import annotations

from arxiv2md_beta.html.serializers.base import SerializerContext, SerializerRegistry
from arxiv2md_beta.html.serializers.block import (
    BlockquoteSerializer,
    FigureSerializer,
    HeadingSerializer,
    ListSerializer,
    ParagraphSerializer,
    TableSerializer,
)
from arxiv2md_beta.html.serializers.inline import (
    BreakSerializer,
    InlineSerializer,
    InlineTextSerializer,
    LinkSerializer,
    MathSerializer,
    NoteSerializer,
    SubscriptSerializer,
    SuperscriptSerializer,
)


def get_default_registry() -> SerializerRegistry:
    """Get the default serializer registry with all serializers registered."""
    registry = SerializerRegistry()

    # Register inline serializers
    registry.register_inline("em", InlineTextSerializer)
    registry.register_inline("i", InlineTextSerializer)
    registry.register_inline("strong", InlineTextSerializer)
    registry.register_inline("b", InlineTextSerializer)
    registry.register_inline("code", InlineTextSerializer)
    registry.register_inline("a", LinkSerializer)
    registry.register_inline("sup", SuperscriptSerializer)
    registry.register_inline("sub", SubscriptSerializer)
    registry.register_inline("br", BreakSerializer)
    registry.register_inline("math", MathSerializer)
    registry.register_inline("cite", NoteSerializer)
    registry.register_inline("span", NoteSerializer)

    # Register block serializers
    registry.register_block("h1", HeadingSerializer)
    registry.register_block("h2", HeadingSerializer)
    registry.register_block("h3", HeadingSerializer)
    registry.register_block("h4", HeadingSerializer)
    registry.register_block("h5", HeadingSerializer)
    registry.register_block("h6", HeadingSerializer)
    registry.register_block("p", ParagraphSerializer)
    registry.register_block("ul", ListSerializer)
    registry.register_block("ol", ListSerializer)
    registry.register_block("table", TableSerializer)
    registry.register_block("figure", FigureSerializer)
    registry.register_block("blockquote", BlockquoteSerializer)

    return registry


__all__ = [
    "SerializerContext",
    "SerializerRegistry",
    "get_default_registry",
    "InlineSerializer",
    "InlineTextSerializer",
    "LinkSerializer",
    "SuperscriptSerializer",
    "SubscriptSerializer",
    "BreakSerializer",
    "MathSerializer",
    "NoteSerializer",
    "HeadingSerializer",
    "ListSerializer",
    "ParagraphSerializer",
    "TableSerializer",
    "FigureSerializer",
    "BlockquoteSerializer",
]
