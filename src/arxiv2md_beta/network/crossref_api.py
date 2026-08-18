"""Fetch metadata from Crossref API."""

from __future__ import annotations

import re
from datetime import datetime

from loguru import logger

from arxiv2md_beta.network.retry import request_with_retries
from arxiv2md_beta.settings import get_settings


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


async def fetch_crossref_metadata(doi: str) -> dict | None:
    """Fetch metadata from Crossref API.

    Parameters
    ----------
    doi : str
        DOI string (e.g., "10.1234/example" or "10.48550/arXiv.2305.11169")

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

    h = get_settings().http
    api_url = get_settings().urls.crossref_works_template.format(doi=doi_clean)

    r = await request_with_retries(
        api_url,
        headers={"User-Agent": h.user_agent},
        label=f"Crossref {doi_clean}",
    )
    if r is None:
        return None
    try:
        return _parse_crossref_response(r.json())
    except ValueError:
        logger.debug(f"Crossref {doi_clean} returned invalid JSON")
        return None


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
    except Exception:
        return {}
