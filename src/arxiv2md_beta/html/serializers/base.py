"""Base classes for HTML to Markdown serializers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ClassVar, Generic, TypeVar

from bs4 import NavigableString, Tag

T = TypeVar("T")


@dataclass
class SerializerContext:
    """Context passed to serializers during HTML to Markdown conversion.

    This context object contains shared state and configuration that
    serializers may need to access.
    """

    image_map: dict[int, Path] | None = None
    image_stem_map: dict[str, Path] | None = None
    figure_counter: list[int] = field(default_factory=lambda: [0])
    used_image_indices: set[int] = field(default_factory=set)
    images_dir: Path | None = None
    remove_inline_citations: bool = False
    heading_offset: int = 0  # For adjusting heading levels

    def clone(self) -> SerializerContext:
        """Create a shallow copy of the context."""
        return SerializerContext(
            image_map=self.image_map,
            image_stem_map=self.image_stem_map,
            figure_counter=self.figure_counter,
            used_image_indices=self.used_image_indices,
            images_dir=self.images_dir,
            remove_inline_citations=self.remove_inline_citations,
            heading_offset=self.heading_offset,
        )


class BaseSerializer(ABC):
    """Base class for all HTML to Markdown serializers."""

    # Tag names this serializer handles
    TAGS: ClassVar[list[str]] = []

    @abstractmethod
    def serialize(self, tag: Tag, context: SerializerContext) -> str | list[str]:
        """Serialize an HTML tag to Markdown.

        Parameters
        ----------
        tag : Tag
            The BeautifulSoup tag to serialize
        context : SerializerContext
            Shared serialization context

        Returns
        -------
        str | list[str]
            Markdown representation of the tag
        """
        ...

    def can_serialize(self, tag: Tag) -> bool:
        """Check if this serializer can handle the given tag.

        Parameters
        ----------
        tag : Tag
            The tag to check

        Returns
        -------
        bool
            True if this serializer can handle the tag
        """
        return tag.name in self.TAGS


class InlineSerializer(BaseSerializer):
    """Base class for inline element serializers."""

    def serialize_children(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize child elements to a string.

        Parameters
        ----------
        tag : Tag
            The parent tag
        context : SerializerContext
            Serialization context

        Returns
        -------
        str
            Concatenated child content
        """
        parts = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                parts.append(self._serialize_inline(child, context))
        return "".join(parts)

    def _serialize_inline(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize an inline tag. Override in subclasses."""
        return tag.get_text(" ", strip=True)


class BlockSerializer(BaseSerializer):
    """Base class for block element serializers."""

    def serialize_children(
        self, tag: Tag, context: SerializerContext, registry: SerializerRegistry | None = None
    ) -> list[str]:
        """Serialize child elements to a list of blocks.

        Parameters
        ----------
        tag : Tag
            The parent tag
        context : SerializerContext
            Serialization context
        registry : SerializerRegistry | None
            Optional registry for dispatching to child serializers

        Returns
        -------
        list[str]
            List of markdown blocks
        """
        blocks = []
        for child in tag.children:
            if isinstance(child, Tag):
                block = self._serialize_block(child, context, registry)
                if block:
                    if isinstance(block, list):
                        blocks.extend(block)
                    else:
                        blocks.append(block)
        return blocks

    def _serialize_block(
        self, tag: Tag, context: SerializerContext, registry: SerializerRegistry | None = None
    ) -> str | list[str] | None:
        """Serialize a block tag. Override in subclasses or use registry."""
        if registry:
            return registry.serialize_block(tag, context)
        return tag.get_text(" ", strip=True)


class SerializerRegistry:
    """Registry for HTML to Markdown serializers.

    This registry maps HTML tag names to their corresponding serializers,
    allowing for pluggable and extensible HTML to Markdown conversion.
    """

    def __init__(self) -> None:
        """Initialize an empty serializer registry."""
        self._inline_serializers: dict[str, type[InlineSerializer]] = {}
        self._block_serializers: dict[str, type[BlockSerializer]] = {}
        # Import here to avoid circular dependency
        from arxiv2md_beta.html.serializers.inline import DefaultInlineSerializer
        self._default_inline = DefaultInlineSerializer()
        self._default_block = None  # No default for blocks

    def register_inline(self, tag: str, serializer: type[InlineSerializer]) -> None:
        """Register an inline serializer for a tag.

        Parameters
        ----------
        tag : str
            The HTML tag name (e.g., 'em', 'strong')
        serializer : type[InlineSerializer]
            The serializer class
        """
        self._inline_serializers[tag] = serializer

    def register_block(self, tag: str, serializer: type[BlockSerializer]) -> None:
        """Register a block serializer for a tag.

        Parameters
        ----------
        tag : str
            The HTML tag name (e.g., 'p', 'h1')
        serializer : type[BlockSerializer]
            The serializer class
        """
        self._block_serializers[tag] = serializer

    def get_inline_serializer(self, tag: Tag) -> InlineSerializer:
        """Get the inline serializer for a tag.

        Parameters
        ----------
        tag : Tag
            The tag to serialize

        Returns
        -------
        InlineSerializer
            The appropriate serializer (or default if not registered)
        """
        serializer_class = self._inline_serializers.get(tag.name)
        if serializer_class:
            return serializer_class()
        return self._default_inline

    def get_block_serializer(self, tag: Tag) -> BlockSerializer | None:
        """Get the block serializer for a tag.

        Parameters
        ----------
        tag : Tag
            The tag to serialize

        Returns
        -------
        BlockSerializer | None
            The appropriate serializer (or None if not registered)
        """
        serializer_class = self._block_serializers.get(tag.name)
        if serializer_class:
            return serializer_class()
        return self._default_block

    def serialize_inline(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize an inline tag using the appropriate serializer.

        Parameters
        ----------
        tag : Tag
            The tag to serialize
        context : SerializerContext
            Serialization context

        Returns
        -------
        str
            Markdown representation
        """
        serializer = self.get_inline_serializer(tag)
        return serializer.serialize(tag, context)

    def serialize_block(
        self, tag: Tag, context: SerializerContext
    ) -> str | list[str] | None:
        """Serialize a block tag using the appropriate serializer.

        Parameters
        ----------
        tag : Tag
            The tag to serialize
        context : SerializerContext
            Serialization context

        Returns
        -------
        str | list[str] | None
            Markdown representation
        """
        serializer = self.get_block_serializer(tag)
        if serializer:
            return serializer.serialize(tag, context)
        # For unregistered block tags, try to serialize children
        if tag.name in ("div", "section", "article", "span"):
            return self._serialize_container(tag, context)
        return None

    def _serialize_container(
        self, tag: Tag, context: SerializerContext
    ) -> list[str]:
        """Serialize a container element by processing its children."""
        blocks = []
        for child in tag.children:
            if isinstance(child, Tag):
                block = self.serialize_block(child, context)
                if block:
                    if isinstance(block, list):
                        blocks.extend(block)
                    else:
                        blocks.append(block)
        return blocks
