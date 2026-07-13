"""OpenAlex API: resolve works by arXiv DOI for author affiliations and ORCID."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from arxiv2md_beta.network.http import get_http_client
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
    """Fetch a single OpenAlex work record for an arXiv id, or ``None`` if not found.

    Retries transient failures (5xx / 429 / network errors) with exponential
    backoff, matching the arXiv and Crossref fetchers. OpenAlex is the primary
    source for affiliations/ORCID, so a single blip previously dropped all
    author enrichment silently.
    """
    s = get_settings()
    h = s.http
    url = openalex_work_url_for_arxiv(base_id)
    timeout = httpx.Timeout(h.fetch_timeout_s)
    headers = {"User-Agent": h.user_agent, "Accept": "application/json"}

    client = get_http_client()
    last_exc: Exception | None = None
    for attempt in range(h.fetch_max_retries + 1):
        try:
            r = await client.get(url, timeout=timeout, headers=headers)
            if r.status_code == 404:
                return None
            if r.status_code in h.retry_status_codes:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {r.status_code} from OpenAlex", request=r.request, response=r
                )
            else:
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            last_exc = e
        if attempt < h.fetch_max_retries:
            await asyncio.sleep(h.fetch_backoff_s * (2**attempt))

    logger.debug(f"OpenAlex fetch exhausted retries for {base_id}: {last_exc}")
    return None
