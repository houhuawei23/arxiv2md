"""Inline element serializers for HTML to Markdown conversion."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from bs4 import NavigableString, Tag

from arxiv2md_beta.html.serializers.base import InlineSerializer, SerializerContext

if TYPE_CHECKING:
    pass

# Regex for detecting citation links
_CITE_HREF_RE = re.compile(r'#\b(ref|citation|cite|footnote|fn|endnote|note)[-_]?\d+', re.I)

# Regex to extract citation number from #bib.bibN format
_BIB_REF_RE = re.compile(r"#bib\.bib(\d+)")


class InlineTextSerializer(InlineSerializer):
    """Serializer for basic inline text formatting."""

    TAGS = ['em', 'i', 'strong', 'b', 'code']

    _WRAPPERS = {
        'em': '*',
        'i': '*',
        'strong': '**',
        'b': '**',
        'code': '`',
    }

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize inline formatting tags."""
        content = self.serialize_children(tag, context)
        wrapper = self._WRAPPERS.get(tag.name, '')
        return f"{wrapper}{content}{wrapper}"


class LinkSerializer(InlineSerializer):
    """Serializer for anchor/link elements."""

    TAGS = ['a']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize link elements."""
        text = self.serialize_children(tag, context).strip()
        href = tag.get('href', '')

        if not href:
            return text

        # Handle citation links
        if self._is_citation_link(href):
            if context.remove_inline_citations:
                return ''
            ref_anchor = self._extract_citation_ref(href)
            if ref_anchor:
                return f"[{text}](#{ref_anchor})"
            return f"[{text}]"

        # Handle internal arXiv fragment links
        if self._is_internal_fragment(href):
            anchor = self._map_fragment_to_anchor(href)
            if anchor:
                return f"[{text}]({anchor})"

        # Regular link
        return f"[{text}]({href})"

    def _extract_citation_ref(self, href: str) -> str | None:
        """Extract citation reference number from href like #bib.bib7 -> ref-7."""
        m = _BIB_REF_RE.match(href)
        if m:
            return f"ref-{m.group(1)}"
        return None

    def _is_citation_link(self, href: str) -> bool:
        """Check if link is a citation reference."""
        if not href:
            return False
        if '#bib.' in href or href.startswith('#bib'):
            return True
        if _CITE_HREF_RE.search(href):
            return True
        return False

    def _is_internal_fragment(self, href: str) -> bool:
        """Check if link is an internal fragment."""
        if not href or not href.startswith('#'):
            return False
        return not href.startswith('#bib')

    def _map_fragment_to_anchor(self, href: str) -> str | None:
        """Map arXiv fragment to markdown anchor."""
        fragment = href[1:]  # Remove leading #

        # Figure: S1.F1 -> #figure-1
        m = re.match(r'S\d+\.F(\d+)$', fragment)
        if m:
            return f"#figure-{m.group(1)}"

        # Table: S5.T1 -> #table-1
        m = re.match(r'[SA]\d*\.?T(\d+)$', fragment)
        if m:
            return f"#table-{m.group(1)}"

        # Appendix: A1 -> #appendix-a
        m = re.match(r'A(\d+)$', fragment)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 26:
                return f"#appendix-{chr(96 + n)}"

        # Algorithm: alg1 -> #algorithm-1
        m = re.match(r'alg(\d+)$', fragment)
        if m:
            return f"#algorithm-{m.group(1)}"

        # Section: S1 -> #section-1
        m = re.match(r'S(\d+)$', fragment)
        if m:
            return f"#section-{m.group(1)}"

        # Subsection: S4.SS1 -> #section-4-1
        m = re.match(r'S(\d+)\.SS(\d+)$', fragment)
        if m:
            return f"#section-{m.group(1)}-{m.group(2)}"

        return None


class SuperscriptSerializer(InlineSerializer):
    """Serializer for superscript elements."""

    TAGS = ['sup']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize superscript."""
        text = self.serialize_children(tag, context).strip()
        return f"^{text}" if text else ""


class SubscriptSerializer(InlineSerializer):
    """Serializer for subscript elements."""

    TAGS = ['sub']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize subscript."""
        text = self.serialize_children(tag, context).strip()
        return f"_{text}" if text else ""


class BreakSerializer(InlineSerializer):
    """Serializer for line break elements."""

    TAGS = ['br']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize line break."""
        return "\n"


class MathSerializer(InlineSerializer):
    """Serializer for math elements."""

    TAGS = ['math']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize math element."""
        # Try to get LaTeX from annotation
        annotation = tag.find('annotation', attrs={'encoding': 'application/x-tex'})
        if annotation and annotation.text:
            latex = annotation.text.strip()
            return f"${latex}$"
        # Fallback to text content
        return f"${tag.get_text(' ', strip=True)}$"


class NoteSerializer(InlineSerializer):
    """Serializer for footnote/note elements."""

    TAGS = ['cite', 'span']

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize note/cite elements."""
        # Check if it's a note
        classes = ' '.join(tag.get('class', []))
        if 'ltx_note' in classes:
            text = self.serialize_children(tag, context).strip()
            return f"({text})" if text else ""
        # cite element
        return self.serialize_children(tag, context)


# Default inline serializer that handles unregistered tags
class DefaultInlineSerializer(InlineSerializer):
    """Default inline serializer for unregistered tags."""

    TAGS = []  # Catch-all for unregistered tags

    def serialize(self, tag: Tag, context: SerializerContext) -> str:
        """Serialize inline element."""
        return tag.get_text(' ', strip=True)
