"""OpenAlex API: resolve works by arXiv DOI for author affiliations and ORCID."""

from __future__ import annotations

from typing import Any

import httpx

from loguru import logger

from arxiv2md_beta.settings import get_settings


def arxiv_base_id(arxiv_id: str) -> str:
    """Strip version suffix from arXiv id."""
    return arxiv_id.split("v")[0].strip() if "v" in arxiv_id else arxiv_id.strip()


def openalex_work_url_for_arxiv(base_id: str) -> str:
    """HTTPS OpenAlex work URL using DataCite DOI for arXiv eprints."""
    # https://arxiv.org/help/doi
    doi = f"https://doi.org/10.48550/arXiv.{base_id}"
    return f"https://api.openalex.org/works/{doi}"


async def fetch_openalex_work_for_arxiv(base_id: str) -> dict[str, Any] | None:
    """Fetch a single OpenAlex work record for an arXiv id, or ``None`` if not found."""
    s = get_settings()
    h = s.http
    url = openalex_work_url_for_arxiv(base_id)
    timeout = httpx.Timeout(h.fetch_timeout_s)
    headers = {"User-Agent": h.user_agent, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.debug(f"OpenAlex HTTP error for {base_id}: {e}")
        return None
    except httpx.RequestError as e:
        logger.debug(f"OpenAlex request failed for {base_id}: {e}")
        return None
