"""Parse citations from HTML bibliography sections."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from arxiv2md_beta.citations.resolver import extract_identifiers
from arxiv2md_beta.utils.logging_config import get_logger

if TYPE_CHECKING:
    from arxiv2md_beta.citations.models import ParsedCitation

logger = get_logger()


def parse_citations_from_html(html: str) -> list["ParsedCitation"]:
    """Parse citations from an HTML bibliography section.

    Parameters
    ----------
    html : str
        HTML content containing bibliography

    Returns
    -------
    list[ParsedCitation]
        List of parsed citations
    """
    from arxiv2md_beta.citations.models import ParsedCitation

    soup = BeautifulSoup(html, "html.parser")
    citations = []

    # Try to find bibliography section
    bib_section = soup.find("section", class_=re.compile(r"ltx_bibliography"))
    if bib_section:
        # Look for list items in bibliography
        for i, item in enumerate(bib_section.find_all("li")):
            text = item.get_text(" ", strip=True)
            if text:
                key = f"bib{i + 1}"
                # Try to find id attribute
                if item.get("id"):
                    key = item.get("id").replace("bib.", "")

                identifiers = extract_identifiers(text)
                citations.append(ParsedCitation(
                    key=key,
                    text=text,
                    identifiers=identifiers,
                ))

        # If no list items found, try paragraphs or divs
        if not citations:
            for i, item in enumerate(bib_section.find_all(["p", "div", "span"], class_=re.compile(r"ltx_bibitem"))):
                text = item.get_text(" ", strip=True)
                if text:
                    key = f"bib{i + 1}"
                    identifiers = extract_identifiers(text)
                    citations.append(ParsedCitation(
                        key=key,
                        text=text,
                        identifiers=identifiers,
                    ))

    # If no bibliography section, try looking for cite elements throughout
    if not citations:
        for i, cite in enumerate(soup.find_all("cite")):
            text = cite.get_text(" ", strip=True)
            if text:
                identifiers = extract_identifiers(text)
                citations.append(ParsedCitation(
                    key=f"cite{i + 1}",
                    text=text,
                    identifiers=identifiers,
                ))

    logger.info(f"Parsed {len(citations)} citations from HTML")
    return citations


def parse_citations_from_text(text: str) -> list["ParsedCitation"]:
    """Parse citations from plain text bibliography.

    Parameters
    ----------
    text : str
        Plain text bibliography

    Returns
    -------
    list[ParsedCitation]
        List of parsed citations
    """
    from arxiv2md_beta.citations.models import ParsedCitation

    citations = []

    # Split by lines that look like citation starts (e.g., [1], 1., Author et al.)
    # This is a heuristic approach
    lines = text.split("\n")
    current_entry = ""
    entry_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if this looks like a new citation entry
        # Pattern: [number], number., or starts with author name
        if re.match(r"^\[?\d+\]?[.\s]", line) or re.match(r"^[A-Z][a-z]+,", line):
            # Save previous entry if exists
            if current_entry:
                entry_num += 1
                identifiers = extract_identifiers(current_entry)
                citations.append(ParsedCitation(
                    key=f"ref{entry_num}",
                    text=current_entry,
                    identifiers=identifiers,
                ))
            current_entry = line
        else:
            # Continue previous entry
            current_entry += " " + line

    # Don't forget the last entry
    if current_entry:
        entry_num += 1
        identifiers = extract_identifiers(current_entry)
        citations.append(ParsedCitation(
            key=f"ref{entry_num}",
            text=current_entry,
            identifiers=identifiers,
        ))

    logger.info(f"Parsed {len(citations)} citations from text")
    return citations
