"""Block element serializers for HTML to Markdown conversion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from bs4 import NavigableString, Tag

from arxiv2md_beta.html.serializers.base import BlockSerializer, SerializerContext
from arxiv2md_beta.html.serializers.inline import (
    InlineSerializer,
    InlineTextSerializer,
    LinkSerializer,
    SuperscriptSerializer,
    SubscriptSerializer,
    BreakSerializer,
)

# Map inline tags to their serializers
_INLINE_SERIALIZERS: dict[str, type[InlineSerializer]] = {
    'em': InlineTextSerializer,
    'i': InlineTextSerializer,
    'strong': InlineTextSerializer,
    'b': InlineTextSerializer,
    'code': InlineTextSerializer,
    'a': LinkSerializer,
    'sup': SuperscriptSerializer,
    'sub': SubscriptSerializer,
    'br': BreakSerializer,
}


def _serialize_inline_element(tag: Tag, context: SerializerContext) -> str:
    """Serialize an inline element using the appropriate serializer."""
    serializer_class = _INLINE_SERIALIZERS.get(tag.name, InlineSerializer)
    serializer = serializer_class()
    return serializer.serialize(tag, context)

if TYPE_CHECKING:
    pass


class HeadingSerializer(BlockSerializer):
    """Serializer for heading elements (h1-h6)."""

    TAGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize heading element."""
        level = int(tag.name[1]) + context.heading_offset
        level = max(1, min(6, level))  # Clamp to 1-6

        text = self._get_text_content(tag, context)
        if not text:
            return ""

        return f"{'#' * level} {text}"

    def _get_text_content(self, tag: Tag, context: SerializerContext) -> str:
        """Extract text content from heading."""
        parts = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                parts.append(child.get_text(' ', strip=True))
        text = ' '.join(parts).strip()
        # Normalize whitespace
        return re.sub(r'\s+', ' ', text)


class ParagraphSerializer(BlockSerializer):
    """Serializer for paragraph elements."""

    TAGS = ['p']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize paragraph element."""
        text_parts = []

        for child in tag.children:
            if isinstance(child, NavigableString):
                text_parts.append(str(child))
            elif isinstance(child, Tag):
                # Check if it's an inline figure
                if self._is_inline_figure(child):
                    continue  # Skip for now, handle separately
                text_parts.append(_serialize_inline_element(child, context))

        text = ''.join(text_parts)
        text = self._cleanup_text(text)

        return text if text else ""

    def _is_inline_figure(self, tag: Tag) -> bool:
        """Check if tag is an inline figure."""
        if tag.name not in ('span', 'div'):
            return False
        classes = ' '.join(tag.get('class', []))
        return 'ltx_figure' in classes and tag.find('img') is not None

    def _cleanup_text(self, text: str) -> str:
        """Clean up paragraph text."""
        # Collapse multiple whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


class ListSerializer(BlockSerializer):
    """Serializer for list elements (ul, ol)."""

    TAGS = ['ul', 'ol']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize list element."""
        items = []
        for i, li in enumerate(tag.find_all('li', recursive=False)):
            item_text = self._serialize_list_item(li, 0, context)
            if item_text:
                items.append(item_text)
        return '\n'.join(items) if items else ""

    def _serialize_list_item(self, li: Tag, indent: int, context: SerializerContext) -> str:
        """Serialize a list item."""
        # Get item text and nested lists
        text_parts = []
        nested_lists = []

        for child in li.children:
            if isinstance(child, NavigableString):
                text_parts.append(str(child))
            elif isinstance(child, Tag):
                if child.name in ('ul', 'ol'):
                    nested_lists.append(child)
                else:
                    text_parts.append(_serialize_inline_element(child, context))

        text = ''.join(text_parts)
        text = re.sub(r'\s+', ' ', text).strip()

        prefix = '  ' * indent + '- '
        lines = [prefix + text if text else prefix.rstrip()]

        # Add nested list items
        for nested in nested_lists:
            for nested_li in nested.find_all('li', recursive=False):
                nested_text = self._serialize_list_item(nested_li, indent + 1, context)
                if nested_text:
                    lines.append(nested_text)

        return '\n'.join(lines)


class TableSerializer(BlockSerializer):
    """Serializer for table elements."""

    TAGS = ['table']

    _EQUATION_TABLE_RE = re.compile(r"ltx_equationgroup|ltx_eqn_align|ltx_eqn_table")

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize table element."""
        classes = ' '.join(tag.get('class', []))

        # Handle equation tables
        if self._EQUATION_TABLE_RE.search(classes):
            return self._serialize_equation_table(tag, context)

        # Handle regular tables
        return self._serialize_regular_table(tag, context)

    def _serialize_equation_table(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize equation table."""
        eqn_text = tag.get_text(' ', strip=True)
        if not eqn_text:
            return ""

        # Simplify and format equation
        eqn_text = self._simplify_equation(eqn_text)
        return f"$$\n{eqn_text}\n$$"

    def _simplify_equation(self, text: str) -> str:
        """Simplify equation text for markdown."""
        # Remove trailing equation numbers
        text = re.sub(r'\s*\((\d+)\)\s*$', r'(\1)', text)
        # Remove outer dollars if present
        text = text.strip()
        if text.startswith('$') and text.endswith('$'):
            text = text[1:-1]
        return text

    def _serialize_regular_table(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize regular data table."""
        rows = []

        # Extract rows from thead, tbody, or directly
        for tbody in tag.find_all(['tbody', 'thead', 'tfoot'], recursive=False):
            for row in tbody.find_all('tr', recursive=False):
                cells = self._extract_cells(row, context)
                if cells:
                    rows.append(cells)

        # If no tbody structure, look for rows directly
        if not rows:
            for row in tag.find_all('tr', recursive=False):
                cells = self._extract_cells(row, context)
                if cells:
                    rows.append(cells)

        return self._format_pipe_table(rows)

    def _extract_cells(self, row: Tag, context: SerializerContext) -> list[str]:
        """Extract cell values from a table row."""
        cells = []
        for cell in row.find_all(['th', 'td'], recursive=False):
            text = self._get_cell_text(cell, context)
            cells.append(text)
        return cells

    def _get_cell_text(self, cell: Tag, context: SerializerContext) -> str:
        """Get text content from a table cell."""
        parts = []
        for child in cell.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                parts.append(_serialize_inline_element(child, context))
        text = ''.join(parts)
        text = re.sub(r'\s+', ' ', text).strip()
        # Replace newlines with <br> for table cells
        return text.replace('\n', '<br>')

    def _format_pipe_table(self, rows: list[list[str]]) -> str:
        """Format rows as a GitHub-flavored markdown pipe table."""
        if not rows:
            return ""

        max_cols = max(len(row) for row in rows)

        # Normalize row lengths
        normalized = [row + [''] * (max_cols - len(row)) for row in rows]

        lines = []

        # Header row
        lines.append('| ' + ' | '.join(normalized[0]) + ' |')

        # Separator
        lines.append('| ' + ' | '.join('---' for _ in normalized[0]) + ' |')

        # Data rows
        for row in normalized[1:]:
            lines.append('| ' + ' | '.join(row) + ' |')

        return '\n'.join(lines)


class BlockquoteSerializer(BlockSerializer):
    """Serializer for blockquote elements."""

    TAGS = ['blockquote']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize blockquote element."""
        parts = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                parts.append(_serialize_inline_element(child, context))

        text = ''.join(parts)
        text = re.sub(r'\s+', ' ', text).strip()

        if text:
            return '> ' + text
        return ""


class FigureSerializer(BlockSerializer):
    """Serializer for figure elements."""

    TAGS = ['figure']

    _FIGURE_CAPTION_RE = re.compile(r"Figure\s+(\d+)\s*[:.]", re.I)
    _TABLE_CAPTION_RE = re.compile(r"Table\s+(\d+)\s*[:.]", re.I)
    _ALGORITHM_CAPTION_RE = re.compile(r"Algorithm\s+(\d+)\s*[:.\s]", re.I)

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize figure element."""
        classes = ' '.join(tag.get('class', []))

        if 'ltx_table' in classes:
            return self._serialize_table_figure(tag, context)
        elif 'ltx_float_algorithm' in classes or 'ltx_algorithm' in classes:
            return self._serialize_algorithm_figure(tag, context)
        else:
            return self._serialize_image_figure(tag, context)

    def _serialize_table_figure(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize table figure."""
        caption_tag = tag.find('figcaption') or tag.find('span', class_=re.compile(r'ltx_caption'))
        caption = self._get_caption_text(caption_tag) if caption_tag else ""

        # Find table
        table = tag.find('table')
        if table:
            table_serializer = TableSerializer()
            table_md = table_serializer.serialize(table, context)

            lines = []
            m = self._TABLE_CAPTION_RE.match(caption)
            if m:
                lines.append(f'<a id="table-{m.group(1)}"></a>')
                lines.append("")
            if caption:
                lines.append(f"> {caption}")
                lines.append("")
            lines.append(table_md)
            return '\n'.join(lines)

        return f"> Table: {caption}" if caption else ""

    def _serialize_algorithm_figure(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize algorithm figure."""
        caption_tag = tag.find('figcaption') or tag.find('span', class_=re.compile(r'ltx_caption'))
        caption = self._get_caption_text(caption_tag) if caption_tag else ""

        lines = []
        m = self._ALGORITHM_CAPTION_RE.match(caption)
        if m:
            lines.append(f'<a id="algorithm-{m.group(1)}"></a>')
            lines.append("")
        if caption:
            lines.append(f"**{caption}**")

        # Find listings
        for listing in tag.find_all('div', class_=re.compile(r'ltx_listing')):
            if 'ltx_listingline' not in ' '.join(listing.get('class', [])):
                code = self._serialize_listing(listing)
                if code:
                    lines.append(code)

        return '\n'.join(lines)

    def _serialize_image_figure(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize image figure."""
        caption_tag = tag.find('figcaption') or tag.find('span', class_=re.compile(r'ltx_caption'))
        caption = self._get_caption_text(caption_tag) if caption_tag else ""

        # Find images
        imgs = tag.find_all('img')
        if not imgs:
            return ""

        lines = []

        # Add figure anchor
        m = self._FIGURE_CAPTION_RE.match(caption)
        if m:
            lines.append(f'<a id="figure-{m.group(1)}"></a>')
            lines.append("")

        # Create image markdown
        if len(imgs) == 1:
            src = imgs[0].get('src', '')
            alt = imgs[0].get('alt', f"Figure {m.group(1) if m else ''}".strip())
            lines.append(f"![{alt}]({src})")
        else:
            # Multi-panel figure
            lines.append('<div align="center">')
            width = "45%" if len(imgs) == 2 else f"{max(14, min(90 // len(imgs), 45))}%"
            for img in imgs:
                src = img.get('src', '')
                alt = img.get('alt', 'Figure panel')
                lines.append(f'  <img src="{src}" width="{width}" alt="{alt}" />')
            lines.append('</div>')

        if caption:
            lines.append("")
            lines.append(f"> {caption}")

        return '\n'.join(lines)

    def _get_caption_text(self, caption_tag: Tag | None) -> str:
        """Extract caption text."""
        if not caption_tag:
            return ""
        text = caption_tag.get_text(' ', strip=True)
        return re.sub(r'\s+', ' ', text)

    def _serialize_listing(self, listing: Tag) -> str:
        """Serialize code listing."""
        # Look for base64 encoded data
        data_div = listing.find('div', class_=re.compile(r'ltx_listing_data'))
        if data_div:
            a = data_div.find('a', href=re.compile(r'^data:text/plain'))
            if a and a.get('href'):
                # Decode base64 data
                import base64
                href = a['href']
                if ';base64,' in href:
                    _, b64 = href.split(';base64,', 1)
                    try:
                        decoded = base64.b64decode(b64).decode('utf-8')
                        return f"```text\n{decoded.rstrip()}\n```"
                    except Exception:
                        pass

        # Fall back to line-by-line
        lines = []
        for line in listing.find_all('div', class_=re.compile(r'ltx_listingline')):
            text = line.get_text(' ', strip=True)
            lines.append(text)

        if lines:
            content = "\n".join(lines)
            return f"```text\n{content}\n```"
        return ""
