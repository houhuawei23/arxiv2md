"""Tests for the new pluggable HTML to Markdown serializers."""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from arxiv2md_beta.html.serializers import (
    get_default_registry,
    SerializerContext,
    HeadingSerializer,
    ListSerializer,
    ParagraphSerializer,
    TableSerializer,
    FigureSerializer,
)


class TestSerializerRegistry:
    """Tests for the serializer registry."""

    def test_get_default_registry(self):
        """Test getting the default registry."""
        registry = get_default_registry()
        assert registry is not None
        # Check that common serializers are registered
        assert registry.get_block_serializer(
            BeautifulSoup("<p>test</p>", "html.parser").find("p")
        ) is not None

    def test_serialize_heading(self):
        """Test heading serialization via registry."""
        registry = get_default_registry()
        context = SerializerContext()

        soup = BeautifulSoup("<h2>Test Heading</h2>", "html.parser")
        result = registry.serialize_block(soup.find("h2"), context)

        assert result == "## Test Heading"

    def test_serialize_paragraph(self):
        """Test paragraph serialization via registry."""
        registry = get_default_registry()
        context = SerializerContext()

        soup = BeautifulSoup("<p>Test paragraph with <strong>bold</strong> text.</p>", "html.parser")
        result = registry.serialize_block(soup.find("p"), context)

        assert "Test paragraph" in result
        assert "**bold**" in result

    def test_serialize_list(self):
        """Test list serialization via registry."""
        registry = get_default_registry()
        context = SerializerContext()

        html = """
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = registry.serialize_block(soup.find("ul"), context)

        assert "- Item 1" in result
        assert "- Item 2" in result


class TestHeadingSerializer:
    """Tests for HeadingSerializer."""

    def test_h1_serialization(self):
        """Test h1 heading serialization."""
        serializer = HeadingSerializer()
        context = SerializerContext()

        soup = BeautifulSoup("<h1>Title</h1>", "html.parser")
        result = serializer.serialize(soup.find("h1"), context)

        assert result == "# Title"

    def test_h2_serialization(self):
        """Test h2 heading serialization."""
        serializer = HeadingSerializer()
        context = SerializerContext()

        soup = BeautifulSoup("<h2>Section</h2>", "html.parser")
        result = serializer.serialize(soup.find("h2"), context)

        assert result == "## Section"

    def test_heading_with_offset(self):
        """Test heading serialization with level offset."""
        serializer = HeadingSerializer()
        context = SerializerContext(heading_offset=1)

        soup = BeautifulSoup("<h1>Title</h1>", "html.parser")
        result = serializer.serialize(soup.find("h1"), context)

        assert result == "## Title"  # h1 becomes h2 with offset


class TestListSerializer:
    """Tests for ListSerializer."""

    def test_unordered_list(self):
        """Test unordered list serialization."""
        serializer = ListSerializer()
        context = SerializerContext()

        html = """
        <ul>
            <li>First</li>
            <li>Second</li>
        </ul>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = serializer.serialize(soup.find("ul"), context)

        assert "- First" in result
        assert "- Second" in result

    def test_ordered_list(self):
        """Test ordered list serialization."""
        serializer = ListSerializer()
        context = SerializerContext()

        html = """
        <ol>
            <li>First</li>
            <li>Second</li>
        </ol>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = serializer.serialize(soup.find("ol"), context)

        # Both ol and ul use - in markdown
        assert "- First" in result
        assert "- Second" in result

    def test_nested_list(self):
        """Test nested list serialization."""
        serializer = ListSerializer()
        context = SerializerContext()

        html = """
        <ul>
            <li>Parent
                <ul>
                    <li>Child</li>
                </ul>
            </li>
        </ul>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = serializer.serialize(soup.find("ul"), context)

        assert "- Parent" in result
        assert "  - Child" in result


class TestParagraphSerializer:
    """Tests for ParagraphSerializer."""

    def test_simple_paragraph(self):
        """Test simple paragraph serialization."""
        serializer = ParagraphSerializer()
        context = SerializerContext()

        soup = BeautifulSoup("<p>Test paragraph.</p>", "html.parser")
        result = serializer.serialize(soup.find("p"), context)

        assert result == "Test paragraph."

    def test_paragraph_with_formatting(self):
        """Test paragraph with inline formatting."""
        serializer = ParagraphSerializer()
        context = SerializerContext()

        html = "<p>Text with <em>italic</em> and <strong>bold</strong>.</p>"
        soup = BeautifulSoup(html, "html.parser")
        result = serializer.serialize(soup.find("p"), context)

        assert "*italic*" in result
        assert "**bold**" in result


class TestTableSerializer:
    """Tests for TableSerializer."""

    def test_simple_table(self):
        """Test simple table serialization."""
        serializer = TableSerializer()
        context = SerializerContext()

        html = """
        <table>
            <tr><th>A</th><th>B</th></tr>
            <tr><td>1</td><td>2</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = serializer.serialize(soup.find("table"), context)

        assert "| A | B |" in result
        assert "| 1 | 2 |" in result
        assert "---" in result  # Separator row (format is | --- | --- |)

    def test_table_with_tbody(self):
        """Test table with tbody structure."""
        serializer = TableSerializer()
        context = SerializerContext()

        html = """
        <table>
            <thead>
                <tr><th>Header</th></tr>
            </thead>
            <tbody>
                <tr><td>Data</td></tr>
            </tbody>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = serializer.serialize(soup.find("table"), context)

        assert "| Header |" in result
        assert "| Data |" in result


class TestSerializerContext:
    """Tests for SerializerContext."""

    def test_context_creation(self):
        """Test creating serializer context."""
        context = SerializerContext()

        assert context.image_map is None
        assert context.figure_counter == [0]
        assert context.used_image_indices == set()

    def test_context_clone(self):
        """Test cloning serializer context."""
        original = SerializerContext(
            image_map={0: Path("test.png")},
            figure_counter=[5],
        )
        clone = original.clone()

        assert clone.image_map == original.image_map
        assert clone.figure_counter == original.figure_counter
        # But they should be the same object for mutable fields
        assert clone.figure_counter is original.figure_counter
