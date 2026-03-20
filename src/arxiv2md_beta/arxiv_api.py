"""Fetch metadata from arXiv API."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import httpx

from arxiv2md_beta.config import (
    ARXIV2MD_BETA_FETCH_BACKOFF_S,
    ARXIV2MD_BETA_FETCH_MAX_RETRIES,
    ARXIV2MD_BETA_FETCH_TIMEOUT_S,
    ARXIV2MD_BETA_USER_AGENT,
)

_RETRY_STATUS = {429, 500, 502, 503, 504}


async def fetch_arxiv_metadata(arxiv_id: str) -> dict[str, str | list | dict | None]:
    """Fetch metadata from arXiv API and optionally enrich with Crossref API.

    Parameters
    ----------
    arxiv_id : str
        arXiv ID (e.g., "2501.11120" or "2501.11120v1")

    Returns
    -------
    dict
        Metadata including title, authors, published date, etc., enriched with Crossref data if available
    """
    # Remove version suffix for API query
    base_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id

    api_url = f"http://export.arxiv.org/api/query?id_list={base_id}"

    timeout = httpx.Timeout(ARXIV2MD_BETA_FETCH_TIMEOUT_S)
    headers = {"User-Agent": ARXIV2MD_BETA_USER_AGENT}
    last_exc: Exception | None = None

    for attempt in range(ARXIV2MD_BETA_FETCH_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                response = await client.get(api_url)

            if response.status_code in _RETRY_STATUS:
                last_exc = RuntimeError(f"HTTP {response.status_code} from arXiv API")
            else:
                response.raise_for_status()
                arxiv_metadata = _parse_api_response(response.text)
                
                # Try to enrich with Crossref API if DOI is available
                doi = arxiv_metadata.get("doi")
                if doi:
                    try:
                        from arxiv2md_beta.crossref_api import fetch_crossref_metadata, is_arxiv_doi
                        
                        # Skip arXiv DOIs
                        if not is_arxiv_doi(doi):
                            crossref_metadata = await fetch_crossref_metadata(doi)
                            if crossref_metadata:
                                # Merge metadata: arXiv as base, Crossref as supplement
                                merged = _merge_metadata(arxiv_metadata, crossref_metadata)
                                return merged
                    except Exception as e:
                        # Crossref failure should not break the flow
                        from loguru import logger
                        logger.debug(f"Failed to fetch Crossref metadata for DOI {doi}: {e}")
                
                return arxiv_metadata
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            last_exc = exc

        if attempt < ARXIV2MD_BETA_FETCH_MAX_RETRIES:
            backoff = ARXIV2MD_BETA_FETCH_BACKOFF_S * (2**attempt)
            await asyncio.sleep(backoff)

    # Return empty metadata if failed
    return {
        "title": None,
        "published": None,
        "submission_date": None,
    }


def _merge_metadata(arxiv_metadata: dict, crossref_metadata: dict) -> dict:
    """Merge arXiv and Crossref metadata, prioritizing Crossref for detailed fields.

    Parameters
    ----------
    arxiv_metadata : dict
        Metadata from arXiv API
    crossref_metadata : dict
        Metadata from Crossref API

    Returns
    -------
    dict
        Merged metadata dictionary
    """
    merged = arxiv_metadata.copy()

    # Merge simple fields (prefer Crossref if available)
    for key in ["volume", "issue", "page", "publisher", "isbn", "issn", "container_title", "crossref_type"]:
        if crossref_metadata.get(key):
            merged[key] = crossref_metadata[key]

    # Merge pages (prefer Crossref)
    if crossref_metadata.get("page"):
        merged["pages"] = crossref_metadata["page"]

    # Authors: prefer Crossref if available (more detailed)
    if crossref_metadata.get("crossref_authors"):
        merged["authors"] = crossref_metadata["crossref_authors"]
    # Otherwise keep arXiv authors but try to enrich with Crossref data if names match
    elif arxiv_metadata.get("authors") and crossref_metadata.get("crossref_authors"):
        # Try to match and merge author information
        arxiv_authors = arxiv_metadata["authors"]
        crossref_authors = crossref_metadata["crossref_authors"]
        merged_authors = []
        for arxiv_author in arxiv_authors:
            arxiv_name = arxiv_author.get("name", "").lower()
            # Try to find matching Crossref author
            matched = False
            for crossref_author in crossref_authors:
                crossref_name = crossref_author.get("name", "").lower()
                # Simple matching: check if last name matches
                if arxiv_name and crossref_name:
                    arxiv_last = arxiv_name.split()[-1] if arxiv_name.split() else ""
                    crossref_last = crossref_name.split()[-1] if crossref_name.split() else ""
                    if arxiv_last and crossref_last and arxiv_last == crossref_last:
                        # Merge: use Crossref details but keep arXiv name format if different
                        merged_author = crossref_author.copy()
                        if arxiv_author.get("name"):
                            merged_author["name"] = arxiv_author["name"]
                        merged_authors.append(merged_author)
                        matched = True
                        break
            if not matched:
                merged_authors.append(arxiv_author)
        merged["authors"] = merged_authors

    # Funding information (only from Crossref)
    if crossref_metadata.get("funding"):
        merged["funding"] = crossref_metadata["funding"]

    # License information (only from Crossref)
    if crossref_metadata.get("license"):
        merged["license"] = crossref_metadata["license"]

    # Keywords: merge arXiv categories and Crossref subjects
    keywords = []
    if arxiv_metadata.get("categories"):
        keywords.extend(arxiv_metadata["categories"])
    if crossref_metadata.get("crossref_subjects"):
        keywords.extend(crossref_metadata["crossref_subjects"])
    # Remove duplicates while preserving order
    seen = set()
    unique_keywords = []
    for kw in keywords:
        kw_lower = str(kw).lower()
        if kw_lower not in seen:
            seen.add(kw_lower)
            unique_keywords.append(kw)
    if unique_keywords:
        merged["keywords_merged"] = unique_keywords

    # Publication name: prefer Crossref container-title, fallback to journal_ref parsing
    if crossref_metadata.get("container_title"):
        merged["publication_name"] = crossref_metadata["container_title"]
    elif arxiv_metadata.get("journal_ref"):
        # Try to extract journal name from journal_ref
        journal_ref = arxiv_metadata["journal_ref"]
        # Simple extraction: take first part before comma or parentheses
        parts = re.split(r"[,\(]", journal_ref)
        if parts:
            merged["publication_name"] = parts[0].strip()

    # Published dates: prefer Crossref if available
    if crossref_metadata.get("published_print_date"):
        merged["published_print_date"] = crossref_metadata["published_print_date"]
        merged["published_print_year"] = crossref_metadata.get("published_print_year")
    if crossref_metadata.get("published_online_date"):
        merged["published_online_date"] = crossref_metadata["published_online_date"]
        merged["published_online_year"] = crossref_metadata.get("published_online_year")

    return merged


def _parse_api_response(xml_content: str) -> dict[str, str | list | dict | None]:
    """Parse arXiv API XML response and extract comprehensive metadata."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_content)
        # Namespaces
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        entry = root.find("atom:entry", ns)
        if entry is None:
            return {"title": None, "published": None, "submission_date": None}

        # Extract title
        title_elem = entry.find("atom:title", ns)
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else None

        # Extract summary/abstract
        summary_elem = entry.find("atom:summary", ns)
        summary = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else None

        # Extract published date
        published_elem = entry.find("atom:published", ns)
        published = published_elem.text.strip() if published_elem is not None and published_elem.text else None

        # Extract updated date
        updated_elem = entry.find("atom:updated", ns)
        updated = updated_elem.text.strip() if updated_elem is not None and updated_elem.text else None

        # Format date as YYYYMMDD
        submission_date = None
        date_str = None
        year = None
        if published:
            try:
                # Parse ISO format: 2025-01-11T18:30:00Z
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                submission_date = dt.strftime("%Y%m%d")
                date_str = dt.strftime("%Y-%m-%d")
                year = dt.strftime("%Y")
            except Exception:
                pass

        # Format updated date
        updated_date = None
        updated_year = None
        if updated:
            try:
                dt_updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                updated_date = dt_updated.strftime("%Y-%m-%d")
                updated_year = dt_updated.strftime("%Y")
            except Exception:
                pass

        # Extract arXiv ID from atom:id (e.g., http://arxiv.org/abs/2301.04104v2)
        arxiv_id_full = None
        arxiv_id = None
        id_elem = entry.find("atom:id", ns)
        if id_elem is not None and id_elem.text:
            arxiv_id_full = id_elem.text
            # Extract ID from URL: http://arxiv.org/abs/2301.04104v2 -> 2301.04104v2
            match = re.search(r"/(\d{4}\.\d{4,5}(?:v\d+)?)$", arxiv_id_full)
            if match:
                arxiv_id = match.group(1)

        # Extract authors
        authors = []
        author_elems = entry.findall("atom:author", ns)
        for author_elem in author_elems:
            name_elem = author_elem.find("atom:name", ns)
            affiliation_elem = author_elem.find("arxiv:affiliation", ns)
            author = {"name": name_elem.text.strip() if name_elem is not None and name_elem.text else None}
            if affiliation_elem is not None and affiliation_elem.text:
                author["affiliation"] = affiliation_elem.text.strip()
            authors.append(author)

        # Extract categories (subjects)
        categories = []
        category_elems = entry.findall("atom:category", ns)
        for cat_elem in category_elems:
            term = cat_elem.get("term")
            if term:
                categories.append(term)

        # Extract primary category
        primary_category_elem = entry.find("arxiv:primary_category", ns)
        primary_category = primary_category_elem.get("term") if primary_category_elem is not None else None

        # Extract comment
        comment_elem = entry.find("arxiv:comment", ns)
        comment = comment_elem.text.strip() if comment_elem is not None and comment_elem.text else None

        # Extract journal reference
        journal_ref_elem = entry.find("arxiv:journal_ref", ns)
        journal_ref = journal_ref_elem.text.strip() if journal_ref_elem is not None and journal_ref_elem.text else None

        # Extract DOI
        doi = None
        doi_elem = entry.find("arxiv:doi", ns)
        if doi_elem is not None and doi_elem.text:
            doi = doi_elem.text.strip()
        else:
            # Try to extract from links
            link_elems = entry.findall("atom:link", ns)
            for link_elem in link_elems:
                if link_elem.get("title") == "doi":
                    href = link_elem.get("href", "")
                    # Extract DOI from URL like http://dx.doi.org/10.1529/biophysj.104.047340
                    match = re.search(r"doi\.org/(.+)", href)
                    if match:
                        doi = match.group(1)

        # Extract links
        abstract_url = None
        pdf_url = None
        link_elems = entry.findall("atom:link", ns)
        for link_elem in link_elems:
            rel = link_elem.get("rel")
            link_type = link_elem.get("type")
            link_title = link_elem.get("title")
            href = link_elem.get("href", "")
            
            if rel == "alternate" and link_type == "text/html":
                abstract_url = href
            elif rel == "related" and link_title == "pdf":
                pdf_url = href

        # Generate BibTeX entry
        bibtex = _generate_bibtex(
            title=title,
            authors=authors,
            year=year,
            arxiv_id=arxiv_id,
            primary_category=primary_category,
            abstract_url=abstract_url,
        )

        # Generate citation
        citation = _generate_citation(
            authors=authors,
            year=year,
            title=title,
            arxiv_id=arxiv_id,
        )

        return {
            "title": title,
            "published": published,
            "updated": updated,
            "submission_date": submission_date,
            "date": date_str,
            "year": year,
            "updated_date": updated_date,
            "updated_year": updated_year,
            "arxiv_id": arxiv_id,
            "arxiv_id_full": arxiv_id_full,
            "summary": summary,
            "authors": authors,
            "categories": categories,
            "primary_category": primary_category,
            "comment": comment,
            "journal_ref": journal_ref,
            "doi": doi,
            "abstract_url": abstract_url,
            "pdf_url": pdf_url,
            "bibtex": bibtex,
            "citation": citation,
        }
    except Exception:
        return {"title": None, "published": None, "submission_date": None}


def _generate_bibtex(
    title: str | None,
    authors: list[dict],
    year: str | None,
    arxiv_id: str | None,
    primary_category: str | None,
    abstract_url: str | None,
) -> str:
    """Generate BibTeX entry from metadata."""
    if not title or not authors or not year or not arxiv_id:
        return ""

    # Generate citation key from first author and year
    first_author = authors[0]["name"] if authors else "author"
    # Extract last name (assume format "First Last" or "Last, First")
    if "," in first_author:
        last_name = first_author.split(",")[0].strip()
    else:
        parts = first_author.split()
        last_name = parts[-1] if parts else "author"
    
    # Clean last name for citation key
    last_name_clean = re.sub(r"[^a-zA-Z]", "", last_name).lower()
    citation_key = f"{last_name_clean}{year}{arxiv_id.split('.')[0]}"

    # Format authors for BibTeX
    author_list = []
    for author in authors:
        name = author.get("name", "")
        if name:
            # Convert "First Last" to "Last, First" for BibTeX
            if "," not in name:
                parts = name.split()
                if len(parts) >= 2:
                    name = f"{parts[-1]}, {' '.join(parts[:-1])}"
            author_list.append(name)
    authors_str = " and ".join(author_list)

    # Format title (escape special characters)
    title_escaped = title.replace("{", "{{").replace("}", "}}")

    # Build BibTeX entry
    bibtex_lines = [
        f"@misc{{{citation_key},",
        f"  author = {{{authors_str}}},",
        f"  title = {{{title_escaped}}},",
        f"  year = {{{year}}},",
    ]

    if arxiv_id:
        # Remove version suffix for eprint
        base_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
        bibtex_lines.append(f"  eprint = {{{base_id}}},")
        bibtex_lines.append("  archivePrefix = {arXiv},")

    if primary_category:
        bibtex_lines.append(f"  primaryClass = {{{primary_category}}},")

    if abstract_url:
        bibtex_lines.append(f"  url = {{{abstract_url}}},")

    bibtex_lines.append("}")

    return "\n".join(bibtex_lines)


def _generate_citation(
    authors: list[dict],
    year: str | None,
    title: str | None,
    arxiv_id: str | None,
) -> str:
    """Generate short citation string."""
    if not authors or not title:
        return ""

    # Format authors
    if len(authors) == 1:
        author_str = authors[0].get("name", "Unknown")
    elif len(authors) == 2:
        author_str = f"{authors[0].get('name', 'Unknown')} and {authors[1].get('name', 'Unknown')}"
    else:
        author_str = f"{authors[0].get('name', 'Unknown')} et al."

    # Truncate title if too long
    title_short = title
    if len(title) > 60:
        title_short = title[:57] + "..."

    # Build citation
    parts = [author_str]
    if year:
        parts.append(f"({year})")
    parts.append(f"{title_short}")
    if arxiv_id:
        parts.append(f"arXiv:{arxiv_id}")

    return ". ".join(parts) + "."
