"""OpenAlex API: resolve works by arXiv DOI for author affiliations and ORCID."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from arxiv2md_beta.network.retry import request_with_retries
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.arxiv_ids import strip_version


def arxiv_base_id(arxiv_id: str) -> str:
    """Strip version suffix from arXiv id."""
    return strip_version(arxiv_id)


def openalex_work_url_for_arxiv(base_id: str) -> str:
    """HTTPS OpenAlex work URL using DataCite DOI for arXiv eprints."""
    # https://arxiv.org/help/doi
    doi = f"https://doi.org/10.48550/arXiv.{base_id}"
    return f"https://api.openalex.org/works/{doi}"


async def fetch_openalex_work_for_arxiv(base_id: str) -> dict[str, Any] | None:
    """Fetch a single OpenAlex work record for an arXiv id, or ``None`` if not found.

    Uses the shared best-effort retry loop (5xx / 429 / network errors retried
    with exponential backoff). OpenAlex is the primary source for
    affiliations/ORCID, so a single blip previously dropped all author
    enrichment silently.
    """
    s = get_settings()
    h = s.http
    r = await request_with_retries(
        openalex_work_url_for_arxiv(base_id),
        headers={"User-Agent": h.user_agent, "Accept": "application/json"},
        timeout=httpx.Timeout(h.fetch_timeout_s),
        label=f"OpenAlex {base_id}",
    )
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        logger.debug(f"OpenAlex {base_id} returned invalid JSON")
        return None
