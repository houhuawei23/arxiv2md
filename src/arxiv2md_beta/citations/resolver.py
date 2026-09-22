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


# Maximum concurrent citation resolutions (each fires Crossref + arXiv API).
_MAX_CONCURRENT_RESOLUTIONS = 6


def _write_bibtex_file(output_path: str, bibtex: str) -> None:
    """Write BibTeX to disk (sync helper for offloading from async caller)."""
    from pathlib import Path

    Path(output_path).write_text(bibtex, encoding="utf-8")


# Regex patterns for extracting identifiers
DOI_PATTERN = re.compile(r"10\.\d{4,}\/[^\s\"'<>]+", re.IGNORECASE)
ARXIV_PATTERN = re.compile(r"arXiv:(\d{4}\.\d{4,}(?:v\d+)?)", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
PMID_PATTERN = re.compile(r"PMID:\s*(\d+)", re.IGNORECASE)


def _strip_doi_punctuation(raw: str) -> str:
    """Strip prose punctuation from a DOI matched out of free text (audit5 R12).

    Trailing ``,``/``;``/``.`` are always punctuation; a trailing ``)`` is only
    removed while unbalanced, so legacy DOIs that legitimately contain
    parentheses (``10.1002/(SICI)…``) survive.
    """
    raw = raw.rstrip(".,;")
    while raw.endswith(")") and raw.count("(") < raw.count(")"):
        raw = raw[:-1].rstrip(".,;")
    return raw.rstrip(".")


class CitationResolver:
    """Resolver for citation metadata."""

    def __init__(self, *, use_cache: bool = True) -> None:
        """Initialize the resolver.

        ``use_cache=False`` (``--no-cache``) also bypasses the Crossref disk
        cache (audit5 S8 F5); the in-process cache always applies.
        """
        self._use_cache = use_cache
        self._cache: dict[str, CitationEntry] = {}
        # Single-flight coalescing: duplicate identifiers in flight resolve
        # once and share the result (two references to the same DOI used to
        # each hit Crossref).
        self._inflight: dict[str, asyncio.Task] = {}
        # DOIs that Crossref could not resolve: remembered so the same dead
        # DOI scattered through one bibliography is fetched once (audit5 R12).
        self._failed_dois: set[str] = set()

    async def resolve_citation(self, parsed: ParsedCitation, index: int = 0) -> CitationEntry:
        """Resolve a parsed citation to a full entry.

        Parameters
        ----------
        parsed : ParsedCitation
            The parsed citation with identifiers
        index : int
            Index for generating unique keys

        Returns:
        -------
        CitationEntry
            Resolved citation entry
        """
        # Check cache by DOI. DOI matching is case-insensitive, so cache and
        # in-flight keys are normalized to lowercase (audit5 R12).
        doi = parsed.identifiers.get("doi")
        if doi:
            cache_key = doi.lower()
            if cache_key in self._cache:
                logger.debug(f"Cache hit for DOI: {doi}")
                return self._cache[cache_key]

        # Try to resolve via DOI first (coalesced per DOI)
        if doi:
            cache_key = doi.lower()
            entry = None
            if cache_key in self._failed_dois:
                pass  # dead DOI: fall through to arXiv / text resolution
            elif cache_key in self._inflight:
                entry = await self._inflight[cache_key]
            else:
                task = asyncio.create_task(self._resolve_by_doi(parsed, index))
                self._inflight[cache_key] = task
                try:
                    entry = await task
                finally:
                    self._inflight.pop(cache_key, None)
            if entry:
                self._cache[cache_key] = entry
                return entry
            self._failed_dois.add(cache_key)

        # Try arXiv ID (coalesced per id, cached like DOIs — the same paper
        # cited twice otherwise triggers two arXiv API calls, audit4 PR4.5)
        if parsed.identifiers.get("arxiv_id"):
            arxiv_id = parsed.identifiers["arxiv_id"]
            cache_key = f"arxiv:{arxiv_id}"
            if cache_key in self._cache:
                logger.debug(f"Cache hit for arXiv id: {arxiv_id}")
                return self._cache[cache_key]
            if arxiv_id in self._inflight:
                entry = await self._inflight[arxiv_id]
            else:
                task = asyncio.create_task(self._resolve_by_arxiv(parsed, index))
                self._inflight[arxiv_id] = task
                try:
                    entry = await task
                finally:
                    self._inflight.pop(arxiv_id, None)
            if entry:
                self._cache[cache_key] = entry
                return entry

        # Fall back to parsed text
        return self._create_entry_from_text(parsed, index)

    async def _resolve_by_doi(self, parsed: ParsedCitation, index: int) -> CitationEntry | None:
        """Resolve citation using DOI."""
        doi = parsed.identifiers.get("doi")
        if not doi:
            return None

        logger.debug(f"Resolving DOI: {doi}")
        metadata = await fetch_crossref_metadata(doi, use_cache=self._use_cache)

        if not metadata:
            return None

        from arxiv2md_beta.citations.models import CitationEntry

        # Build entry from Crossref metadata. Structured given/family fields
        # become "Family, Given" directly — the old display-name form fed the
        # surname heuristic, which mis-split "van der Berg S.", "Y. Chen Jr."
        # and CJK names (audit5 R-14). Both generate_citation_key and the
        # BibTeX author formatter parse the "Family, Given" shape natively.
        authors = []
        for a in metadata.get("crossref_authors", []):
            family, given, suffix = a.get("family"), a.get("given"), a.get("suffix")
            if family:
                name = f"{family}, {given}" if given else family
                if suffix:
                    name = f"{name} {suffix}"
                authors.append(name)
            elif a.get("name"):
                authors.append(a["name"])

        entry = CitationEntry(
            key=generate_citation_key(
                authors,
                metadata.get("published_print_year"),
                metadata.get("container_title"),
                index,
            ),
            # The work's title; container_title is the journal and only falls
            # back here for Crossref responses without a title (audit5 C5).
            title=metadata.get("title") or metadata.get("container_title"),
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

    async def _resolve_by_arxiv(self, parsed: ParsedCitation, index: int) -> CitationEntry | None:
        """Resolve citation using arXiv ID."""
        arxiv_id = parsed.identifiers.get("arxiv_id")
        if not arxiv_id:
            return None

        logger.debug(f"Resolving arXiv ID: {arxiv_id}")

        # Import here to avoid circular imports
        from arxiv2md_beta.network.arxiv_api import (
            author_display_names_from_metadata,
            fetch_arxiv_metadata,
            submission_date_from_new_style_arxiv_id,
        )

        try:
            metadata = await fetch_arxiv_metadata(arxiv_id)
            if not metadata:
                return None

            from arxiv2md_beta.citations.models import CitationEntry

            title = metadata.get("title")
            title = title if isinstance(title, str) else None
            authors = author_display_names_from_metadata(metadata)
            year = metadata.get("year")
            if year is None:
                sd = submission_date_from_new_style_arxiv_id(arxiv_id)
                year = sd[:4] if sd else None
            year = year if isinstance(year, str) else str(year) if year is not None else None

            entry = CitationEntry(
                key=generate_citation_key(authors, year, title, index),
                title=title,
                authors=authors,
                year=year,
                journal="arXiv preprint",
                url=f"https://arxiv.org/abs/{arxiv_id}",
                entry_type="article",
                raw_text=parsed.text,
            )

            return entry
        except Exception as e:
            logger.warning(f"Failed to resolve arXiv ID {arxiv_id}: {e}")
            return None

    def _create_entry_from_text(self, parsed: ParsedCitation, index: int) -> CitationEntry:
        """Create a basic entry from parsed text when no identifiers resolve."""
        from arxiv2md_beta.citations.models import CitationEntry

        # Try to extract year from text. ``group(0)`` — the full match ("2015");
        # ``group(1)`` is just the century alternation ("19"/"20").
        year_match = re.search(r"\b(?:19|20)\d{2}\b", parsed.text)
        year = year_match.group(0) if year_match else None

        return CitationEntry(
            key=parsed.key or f"ref{index}",
            title=parsed.text[:100] + "..." if len(parsed.text) > 100 else parsed.text,
            year=year,
            raw_text=parsed.text,
            entry_type="misc",
        )

    async def resolve_citations(self, parsed_list: list[ParsedCitation]) -> list[CitationEntry]:
        """Resolve multiple citations concurrently.

        Parameters
        ----------
        parsed_list : list[ParsedCitation]
            List of parsed citations

        Returns:
        -------
        list[CitationEntry]
            List of resolved entries
        """
        # Bound concurrency: each citation fires Crossref + arXiv API calls.
        # A paper with 100+ references previously issued 100+ simultaneous
        # requests → 429 rate-limiting / connection exhaustion / IP bans.
        sem = asyncio.Semaphore(_MAX_CONCURRENT_RESOLUTIONS)

        async def _bounded(parsed: ParsedCitation, index: int) -> CitationEntry:
            async with sem:
                return await self.resolve_citation(parsed, index)

        tasks = [_bounded(parsed, index) for index, parsed in enumerate(parsed_list)]
        return await asyncio.gather(*tasks)


def extract_identifiers(text: str) -> dict[str, str]:
    """Extract identifiers from citation text.

    Parameters
    ----------
    text : str
        Citation text

    Returns:
    -------
    dict[str, str]
        Dictionary of identifier type to value
    """
    identifiers = {}

    # Extract DOI
    doi_match = DOI_PATTERN.search(text)
    if doi_match:
        identifiers["doi"] = _strip_doi_punctuation(doi_match.group(0))

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
            # Skip URLs that are just DOIs (any doi.org host form)
            if "doi.org/" not in url:
                identifiers["url"] = url

    return identifiers


async def export_bibtex(
    parsed_citations: list[ParsedCitation],
    output_path: str | None = None,
    *,
    use_cache: bool = True,
) -> str:
    """Export citations to BibTeX format.

    Parameters
    ----------
    parsed_citations : list[ParsedCitation]
        List of parsed citations
    output_path : str | None
        Optional path to write BibTeX file
    use_cache : bool
        Read/write the Crossref disk cache (audit5 S8 F5).

    Returns:
    -------
    str
        BibTeX formatted string
    """
    from arxiv2md_beta.citations.formatter import format_bibtex_database

    resolver = CitationResolver(use_cache=use_cache)
    entries = await resolver.resolve_citations(parsed_citations)

    bibtex = format_bibtex_database(entries)

    if output_path:
        await asyncio.to_thread(_write_bibtex_file, output_path, bibtex)
        logger.info(f"Wrote BibTeX to {output_path}")

    return bibtex
