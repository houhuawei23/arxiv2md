"""Fetch metadata from Crossref API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from loguru import logger

from arxiv2md_beta.network.retry import request_with_retries
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.atomic_io import atomic_write_text_sync


def is_arxiv_doi(doi: str) -> bool:
    """Check if DOI is an arXiv DOI (e.g., 10.48550/arXiv.XXXX).

    Parameters
    ----------
    doi : str
        DOI string

    Returns:
    -------
    bool
        True if it's an arXiv DOI
    """
    if not doi:
        return False
    # Prefix match only: a substring test would misclassify journal DOIs whose
    # path merely contains "arxiv" (e.g. 10.1234/arxiv-study).
    return doi.lower().startswith("10.48550/arxiv")


async def fetch_crossref_metadata(doi: str, *, use_cache: bool = True) -> dict | None:
    """Fetch metadata from Crossref API.

    Results are cached on disk under ``cache.dir/crossref/`` (audit5 S8 F5)
    with the ``cache.ttl_seconds`` TTL, so consecutive runs — a batch of
    papers citing the same references — fetch each DOI once. Misses are
    cached too (negative cache, audit5 R-12): a dead DOI re-fetches only
    after the TTL expires. ``cache.ttl_seconds <= 0`` disables the disk
    cache.

    Parameters
    ----------
    doi : str
        DOI string (e.g., "10.1234/example" or "10.48550/arXiv.2305.11169")
    use_cache : bool
        Read and write the disk cache (``--no-cache`` passes False).

    Returns:
    -------
    dict | None
        Metadata dictionary if successful, None if failed or not found
    """
    if not doi or is_arxiv_doi(doi):
        # arXiv DOIs are usually not in Crossref.
        return None

    # Normalize DOI (remove http://dx.doi.org/ prefix if present)
    doi_clean = doi.strip()
    for prefix in ("http://dx.doi.org/", "https://dx.doi.org/", "http://doi.org/", "https://doi.org/"):
        if doi_clean.startswith(prefix):
            doi_clean = doi_clean[len(prefix) :]
            break

    if use_cache:
        # Offloaded: a cache hit reads + parses a JSON file (sync IO).
        cached = await asyncio.to_thread(_load_disk_cache, doi_clean)
        if cached is not None:
            _hit, value = cached
            return value

    h = get_settings().http
    # DOIs may carry '/', '#' etc.; without quoting the URL is malformed
    # (audit5 R5).
    api_url = get_settings().urls.crossref_works_template.format(doi=quote(doi_clean, safe=""))

    r = await request_with_retries(
        api_url,
        headers={"User-Agent": h.user_agent},
        label=f"Crossref {doi_clean}",
    )
    metadata: dict | None = None
    if r is not None:
        try:
            metadata = _parse_crossref_response(r.json())
        except ValueError:
            logger.debug(f"Crossref {doi_clean} returned invalid JSON")
    if use_cache:
        await asyncio.to_thread(_write_disk_cache, doi_clean, metadata)
    return metadata


# ── Disk cache (audit5 S8 F5) ─────────────────────────────────────────


def _crossref_cache_path(doi_clean: str) -> Path:
    # Hash, not the raw DOI: DOIs may carry '/', ':' or unicode; a hashed
    # name is flat, filesystem-safe, and naturally case-insensitive because
    # the key is lowercased first.
    key = hashlib.sha256(doi_clean.lower().encode("utf-8")).hexdigest()
    return get_settings().resolved_cache_path() / "crossref" / f"{key}.json"


def _load_disk_cache(doi_clean: str) -> tuple[bool, dict | None] | None:
    """Look up *doi_clean* in the on-disk Crossref cache.

    ``None`` = nothing usable cached; ``(True, meta)`` = hit;
    ``(False, None)`` = cached miss (negative cache, audit5 R-12).
    """
    ttl = get_settings().cache.ttl_seconds
    if ttl <= 0:
        return None
    path = _crossref_cache_path(doi_clean)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, int | float) or time.time() - fetched_at > ttl:
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        return True, metadata
    return False, None


def _write_disk_cache(doi_clean: str, metadata: dict | None) -> None:
    """Persist a lookup outcome (positive or negative); best-effort."""
    ttl = get_settings().cache.ttl_seconds
    if ttl <= 0:
        return
    path = _crossref_cache_path(doi_clean)
    payload = {
        "doi": doi_clean,
        "fetched_at": time.time(),
        "found": metadata is not None,
        "metadata": metadata,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text_sync(path, json.dumps(payload, ensure_ascii=False))
    except OSError as e:
        logger.debug(f"Crossref disk cache write failed for {doi_clean}: {e}")


def _parse_crossref_response(json_data: dict) -> dict:
    """Parse Crossref API JSON response.

    Parameters
    ----------
    json_data : dict
        JSON response from Crossref API

    Returns:
    -------
    dict
        Extracted metadata dictionary
    """
    try:
        message = json_data.get("message", {})
        if not message:
            return {}

        metadata = {}

        # Article title (audit5 C5: the ``title`` field, never the journal).
        # The title list is the *work's* title; container-title is where it
        # appeared. Conflating them exported ``title = {Nature}``.
        title = message.get("title", [])
        if title:
            metadata["title"] = title[0] if isinstance(title, list) else title

        # Container title (journal/conference name)
        container_title = message.get("container-title", [])
        if container_title:
            metadata["container_title"] = container_title[0] if isinstance(container_title, list) else container_title

        # Volume, issue, page
        volume = message.get("volume")
        if volume:
            metadata["volume"] = str(volume)

        issue = message.get("issue")
        if issue:
            metadata["issue"] = str(issue)

        page = message.get("page")
        if page:
            metadata["page"] = str(page)

        # Publisher
        publisher = message.get("publisher")
        if publisher:
            metadata["publisher"] = publisher

        # Authors with detailed information
        authors = message.get("author", [])
        if authors:
            crossref_authors = []
            for author in authors:
                author_dict = {}
                given = author.get("given", "")
                family = author.get("family", "")
                if given and family:
                    author_dict["name"] = f"{given} {family}"
                elif family:
                    author_dict["name"] = family
                elif given:
                    author_dict["name"] = given
                # Structured parts survive alongside the display name so the
                # citation layer can skip its surname heuristic (audit5 R-14).
                if given:
                    author_dict["given"] = given
                if family:
                    author_dict["family"] = family
                if author.get("suffix"):
                    author_dict["suffix"] = author["suffix"]

                # ORCID
                orcid_list = author.get("ORCID", "")
                if orcid_list:
                    # ORCID is usually a URL, extract the ID
                    orcid_str = orcid_list if isinstance(orcid_list, str) else orcid_list[0] if orcid_list else ""
                    if orcid_str:
                        # Extract ORCID ID from URL like https://orcid.org/0000-0002-1825-0097
                        match = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", orcid_str)
                        if match:
                            author_dict["orcid"] = match.group(1)

                # Affiliation
                affiliations = author.get("affiliation", [])
                if affiliations:
                    aff_list = []
                    for aff in affiliations:
                        if isinstance(aff, dict):
                            aff_name = aff.get("name", "")
                            if aff_name:
                                aff_list.append(aff_name)
                        elif isinstance(aff, str):
                            aff_list.append(aff)
                    if aff_list:
                        author_dict["affiliation"] = "; ".join(aff_list)

                if author_dict.get("name"):
                    crossref_authors.append(author_dict)
            if crossref_authors:
                metadata["crossref_authors"] = crossref_authors

        # Funding information
        funder_list = message.get("funder", [])
        if funder_list:
            funding = []
            for funder in funder_list:
                funder_dict = {}
                funder_name = funder.get("name")
                if funder_name:
                    funder_dict["funder"] = funder_name
                award = funder.get("award", [])
                if award:
                    if isinstance(award, list) and award:
                        funder_dict["grant_number"] = award[0]
                    elif isinstance(award, str):
                        funder_dict["grant_number"] = award
                if funder_dict:
                    funding.append(funder_dict)
            if funding:
                metadata["funding"] = funding

        # License information
        license_list = message.get("license", [])
        if license_list:
            licenses = []
            for lic in license_list:
                if isinstance(lic, dict):
                    license_url = lic.get("URL", "")
                    license_start = lic.get("start", {})
                    if isinstance(license_start, dict):
                        license_date = license_start.get("date-parts", [[None]])[0]
                        if license_date and len(license_date) >= 1:
                            license_year = license_date[0]
                            if license_url:
                                licenses.append({"url": license_url, "year": str(license_year)})
                    elif license_url:
                        licenses.append({"url": license_url})
            if licenses:
                metadata["license"] = licenses

        # Keywords/subjects
        subject_list = message.get("subject", [])
        if subject_list:
            metadata["crossref_subjects"] = [str(s) for s in subject_list if s]

        # ISBN/ISSN
        isbn_list = message.get("ISBN", [])
        if isbn_list:
            metadata["isbn"] = isbn_list[0] if isinstance(isbn_list, list) else isbn_list

        issn_list = message.get("ISSN", [])
        if issn_list:
            # ISSN can be a list of lists (print and electronic)
            if isinstance(issn_list, list):
                flat_issn = []
                for issn_item in issn_list:
                    if isinstance(issn_item, list):
                        flat_issn.extend(issn_item)
                    else:
                        flat_issn.append(issn_item)
                metadata["issn"] = flat_issn[0] if flat_issn else None
            else:
                metadata["issn"] = issn_list

        # Published dates
        published_print = message.get("published-print", {})
        published_online = message.get("published-online", {})
        if published_print:
            date_parts = published_print.get("date-parts", [[None]])[0]
            if date_parts and len(date_parts) >= 3:
                try:
                    pub_date = datetime(date_parts[0], date_parts[1], date_parts[2])
                    metadata["published_print_date"] = pub_date.strftime("%Y-%m-%d")
                    metadata["published_print_year"] = str(date_parts[0])
                except Exception:
                    pass

        if published_online:
            date_parts = published_online.get("date-parts", [[None]])[0]
            if date_parts and len(date_parts) >= 3:
                try:
                    pub_date = datetime(date_parts[0], date_parts[1], date_parts[2])
                    metadata["published_online_date"] = pub_date.strftime("%Y-%m-%d")
                    metadata["published_online_year"] = str(date_parts[0])
                except Exception:
                    pass

        # Document type
        doc_type = message.get("type")
        if doc_type:
            metadata["crossref_type"] = doc_type

        return metadata
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        # Enrichment is best-effort, but a programming error (KeyError from a
        # renamed field, ...) must not vanish silently (audit4 P2).
        logger.debug(f"Crossref metadata parse degraded: {type(exc).__name__}: {exc}")
        return {}
