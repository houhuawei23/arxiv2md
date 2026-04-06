"""Citation resolver for fetching and enriching citation metadata."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from arxiv2md_beta.citations.formatter import generate_citation_key
from arxiv2md_beta.network.crossref_api import fetch_crossref_metadata
from arxiv2md_beta.utils.logging_config import get_logger

if TYPE_CHECKING:
    from arxiv2md_beta.citations.models import CitationEntry, ParsedCitation

logger = get_logger()


# Regex patterns for extracting identifiers
DOI_PATTERN = re.compile(r"10\.\d{4,}\/[^\s\"'<>]+", re.IGNORECASE)
ARXIV_PATTERN = re.compile(r"arXiv:(\d{4}\.\d{4,}(?:v\d+)?)", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
PMID_PATTERN = re.compile(r"PMID:\s*(\d+)", re.IGNORECASE)


class CitationResolver:
    """Resolver for citation metadata."""

    def __init__(self) -> None:
        """Initialize the resolver."""
        self._cache: dict[str, "CitationEntry"] = {}

    async def resolve_citation(self, parsed: "ParsedCitation", index: int = 0) -> "CitationEntry":
        """Resolve a parsed citation to a full entry.

        Parameters
        ----------
        parsed : ParsedCitation
            The parsed citation with identifiers
        index : int
            Index for generating unique keys

        Returns
        -------
        CitationEntry
            Resolved citation entry
        """
        from arxiv2md_beta.citations.models import CitationEntry

        # Check cache by DOI
        if parsed.identifiers.get("doi"):
            doi = parsed.identifiers["doi"]
            if doi in self._cache:
                logger.debug(f"Cache hit for DOI: {doi}")
                return self._cache[doi]

        # Try to resolve via DOI first
        if parsed.identifiers.get("doi"):
            entry = await self._resolve_by_doi(parsed, index)
            if entry:
                self._cache[parsed.identifiers["doi"]] = entry
                return entry

        # Try arXiv ID
        if parsed.identifiers.get("arxiv_id"):
            entry = await self._resolve_by_arxiv(parsed, index)
            if entry:
                return entry

        # Fall back to parsed text
        return self._create_entry_from_text(parsed, index)

    async def _resolve_by_doi(self, parsed: "ParsedCitation", index: int) -> "CitationEntry" | None:
        """Resolve citation using DOI."""
        doi = parsed.identifiers.get("doi")
        if not doi:
            return None

        logger.debug(f"Resolving DOI: {doi}")
        metadata = await fetch_crossref_metadata(doi)

        if not metadata:
            return None

        from arxiv2md_beta.citations.models import CitationEntry

        # Build entry from Crossref metadata
        authors = []
        if "crossref_authors" in metadata:
            authors = [a.get("name", "") for a in metadata["crossref_authors"] if a.get("name")]

        entry = CitationEntry(
            key=generate_citation_key(authors, metadata.get("published_print_year"), metadata.get("container_title"), index),
            title=metadata.get("container_title"),
            authors=authors,
            year=metadata.get("published_print_year") or metadata.get("published_online_year"),
            journal=metadata.get("container_title"),
            volume=metadata.get("volume"),
            issue=metadata.get("issue"),
            pages=metadata.get("page"),
            doi=doi,
            publisher=metadata.get("publisher"),
            raw_text=parsed.text,
        )

        return entry

    async def _resolve_by_arxiv(self, parsed: "ParsedCitation", index: int) -> "CitationEntry" | None:
        """Resolve citation using arXiv ID."""
        arxiv_id = parsed.identifiers.get("arxiv_id")
        if not arxiv_id:
            return None

        logger.debug(f"Resolving arXiv ID: {arxiv_id}")

        # Import here to avoid circular imports
        from arxiv2md_beta.network.arxiv_api import query_arxiv_api

        try:
            result = await query_arxiv_api(arxiv_id)
            if not result:
                return None

            from arxiv2md_beta.citations.models import CitationEntry

            entry = CitationEntry(
                key=generate_citation_key(result.authors, result.year, result.title, index),
                title=result.title,
                authors=result.authors,
                year=result.year,
                journal="arXiv preprint",
                url=f"https://arxiv.org/abs/{arxiv_id}",
                entry_type="article",
                raw_text=parsed.text,
            )

            return entry
        except Exception as e:
            logger.warning(f"Failed to resolve arXiv ID {arxiv_id}: {e}")
            return None

    def _create_entry_from_text(self, parsed: "ParsedCitation", index: int) -> "CitationEntry":
        """Create a basic entry from parsed text when no identifiers resolve."""
        from arxiv2md_beta.citations.models import CitationEntry

        # Try to extract year from text
        year_match = re.search(r"\b(19|20)\d{2}\b", parsed.text)
        year = year_match.group(1) if year_match else None

        return CitationEntry(
            key=parsed.key or f"ref{index}",
            title=parsed.text[:100] + "..." if len(parsed.text) > 100 else parsed.text,
            year=year,
            raw_text=parsed.text,
            entry_type="misc",
        )

    async def resolve_citations(self, parsed_list: list["ParsedCitation"]) -> list["CitationEntry"]:
        """Resolve multiple citations concurrently.

        Parameters
        ----------
        parsed_list : list[ParsedCitation]
            List of parsed citations

        Returns
        -------
        list[CitationEntry]
            List of resolved entries
        """
        tasks = [
            self.resolve_citation(parsed, index)
            for index, parsed in enumerate(parsed_list)
        ]
        return await asyncio.gather(*tasks)


def extract_identifiers(text: str) -> dict[str, str]:
    """Extract identifiers from citation text.

    Parameters
    ----------
    text : str
        Citation text

    Returns
    -------
    dict[str, str]
        Dictionary of identifier type to value
    """
    identifiers = {}

    # Extract DOI
    doi_match = DOI_PATTERN.search(text)
    if doi_match:
        identifiers["doi"] = doi_match.group(0).rstrip(".")

    # Extract arXiv ID
    arxiv_match = ARXIV_PATTERN.search(text)
    if arxiv_match:
        identifiers["arxiv_id"] = arxiv_match.group(1)

    # Extract PMID
    pmid_match = PMID_PATTERN.search(text)
    if pmid_match:
        identifiers["pmid"] = pmid_match.group(1)

    # Extract URL (only if no DOI found, to avoid duplicates)
    if "doi" not in identifiers:
        url_match = URL_PATTERN.search(text)
        if url_match:
            url = url_match.group(0)
            # Skip URLs that are just DOIs
            if not url.startswith("https://doi.org/"):
                identifiers["url"] = url

    return identifiers


async def export_bibtex(
    parsed_citations: list["ParsedCitation"],
    output_path: str | None = None,
) -> str:
    """Export citations to BibTeX format.

    Parameters
    ----------
    parsed_citations : list[ParsedCitation]
        List of parsed citations
    output_path : str | None
        Optional path to write BibTeX file

    Returns
    -------
    str
        BibTeX formatted string
    """
    from arxiv2md_beta.citations.formatter import format_bibtex_database

    resolver = CitationResolver()
    entries = await resolver.resolve_citations(parsed_citations)

    bibtex = format_bibtex_database(entries)

    if output_path:
        from pathlib import Path
        Path(output_path).write_text(bibtex, encoding="utf-8")
        logger.info(f"Wrote BibTeX to {output_path}")

    return bibtex
