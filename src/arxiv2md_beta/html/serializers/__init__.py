"""HTML to Markdown serializers.

This package contains pluggable serializers for converting HTML elements
to Markdown. Each serializer handles a specific type of HTML element.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arxiv2md_beta.html.serializers.base import SerializerContext, SerializerRegistry
from arxiv2md_beta.html.serializers.inline import InlineSerializer
from arxiv2md_beta.html.serializers.block import (
    HeadingSerializer,
    ListSerializer,
    ParagraphSerializer,
    TableSerializer,
    FigureSerializer,
    BlockquoteSerializer,
)

if TYPE_CHECKING:
    from bs4 import Tag


def get_default_registry() -> SerializerRegistry:
    """Get the default serializer registry with all serializers registered."""
    registry = SerializerRegistry()

    # Register inline serializers
    registry.register_inline("em", InlineSerializer)
    registry.register_inline("i", InlineSerializer)
    registry.register_inline("strong", InlineSerializer)
    registry.register_inline("b", InlineSerializer)
    registry.register_inline("a", InlineSerializer)
    registry.register_inline("code", InlineSerializer)
    registry.register_inline("sup", InlineSerializer)
    registry.register_inline("sub", InlineSerializer)
    registry.register_inline("br", InlineSerializer)

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
    "HeadingSerializer",
    "ListSerializer",
    "ParagraphSerializer",
    "TableSerializer",
    "FigureSerializer",
    "BlockquoteSerializer",
]
