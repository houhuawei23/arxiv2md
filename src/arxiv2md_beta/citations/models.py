"""Data models for citations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CitationEntry:
    """A single citation entry."""

    key: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: str | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    url: str | None = None
    publisher: str | None = None
    booktitle: str | None = None
    entry_type: Literal[
        "article", "book", "inproceedings", "incollection", "techreport", "phdthesis", "misc"
    ] = "article"
    abstract: str | None = None
    note: str | None = None
    raw_text: str | None = None  # Original text from paper

    def to_bibtex(self) -> str:
        """Convert to BibTeX format."""
        from arxiv2md_beta.citations.formatter import format_bibtex_entry
        return format_bibtex_entry(self)


@dataclass
class ParsedCitation:
    """A parsed citation with identifier information."""

    key: str
    text: str
    identifiers: dict[str, str] = field(default_factory=dict)
    # identifiers may contain: doi, arxiv_id, url, pmid, etc.
