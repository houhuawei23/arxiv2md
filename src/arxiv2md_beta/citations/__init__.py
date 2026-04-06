"""Citation resolution and BibTeX export for arxiv2md-beta.

Provides functionality to:
- Parse citations from paper bibliography
- Resolve DOIs to metadata via Crossref
- Export citations to BibTeX format
"""

from __future__ import annotations

from arxiv2md_beta.citations.models import CitationEntry, ParsedCitation
from arxiv2md_beta.citations.resolver import (
    CitationResolver,
    export_bibtex,
    extract_identifiers,
)
from arxiv2md_beta.citations.formatter import format_bibtex_entry, format_bibtex_database
from arxiv2md_beta.citations.html_parser import (
    parse_citations_from_html,
    parse_citations_from_text,
)

__all__ = [
    "CitationEntry",
    "ParsedCitation",
    "CitationResolver",
    "export_bibtex",
    "extract_identifiers",
    "format_bibtex_entry",
    "format_bibtex_database",
    "parse_citations_from_html",
    "parse_citations_from_text",
]
