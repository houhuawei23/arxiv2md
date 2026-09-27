"""Fetch and cache arXiv HTML pages and PDFs.

The transport mechanics (status classification, retries, mirror fallback,
cache freshness, atomic cache writes) live in :mod:`network.download`; this
module adds the HTML/PDF-specific semantics: placeholder-page rejection,
the ar5iv third fallback tier, and the cache→output copy for PDFs.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

import httpx
from loguru import logger

from arxiv2md_beta.exceptions import NetworkError, NonRetryableNetworkError
from arxiv2md_beta.network.download import (
    cache_dir_for,
    download_file_with_retries,
    fetch_text_with_retries,
    is_cache_fresh,
    with_mirror_fallback,
)
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.atomic_io import atomic_write_text


async def fetch_arxiv_html(
    html_url: str,
    *,
    arxiv_id: str,
    version: str | None,
    use_cache: bool = True,
    ar5iv_url: str | None = None,
) -> str:
    """Fetch arXiv HTML and cache it locally.

    Tries html_url first (arxiv.org), then the export mirror (on retry-worthy
    failures), then ar5iv_url if the origin deterministically 404'd.
    """
    html_path = cache_dir_for(arxiv_id, version) / "source.html"

    if use_cache and is_cache_fresh(html_path):
        # Offloaded: a full cached HTML read is sync IO in the hot batch path.
        html_text = await asyncio.to_thread(html_path.read_text, encoding="utf-8")
        try:
            _reject_no_content_placeholder(html_text)
        except NetworkError:
            # Poisoned cache: a placeholder written by an older version (before
            # the write-side guard existed) would fail every run until its TTL
            # expired. Drop it and fall through to a fresh download.
            logger.warning(f"Discarding poisoned HTML cache for {arxiv_id}: placeholder page")
            html_path.unlink(missing_ok=True)
        else:
            return html_text

    async def fetch_from(url: str) -> str:
        text = await fetch_text_with_retries(
            url,
            label=f"fetch HTML {url}",
            not_found_error=_html_not_found,
        )
        _reject_no_content_placeholder(text)
        await atomic_write_text(html_path, text, encoding="utf-8")
        return text

    try:
        return await with_mirror_fallback(html_url, describe="HTML fetch", fetch=fetch_from)
    except NetworkError as primary_error:
        # ar5iv renders from the LaTeX source and can succeed where both arXiv
        # hosts 404 — but only worth trying on a deterministic origin 404.
        if ar5iv_url and isinstance(primary_error, NonRetryableNetworkError) and primary_error.status_code == 404:
            try:
                return await fetch_from(ar5iv_url)
            except (httpx.RequestError, httpx.HTTPStatusError, NetworkError, OSError) as fallback_error:
                logger.warning(f"ar5iv fallback also failed: {fallback_error}")
                raise primary_error from fallback_error
        raise


def _html_not_found() -> NetworkError:
    return NonRetryableNetworkError(
        "This paper does not have an HTML version available on arXiv. "
        "arxiv2md-beta requires papers to be available in HTML format. "
        "Older papers may only be available as PDF.",
        status_code=404,
    )


# Placeholder pages vary in spacing/case ("<title> No content available "
# "</title>", "<title>No content available</title>", …); exact matching let
# variants through and produced empty "No content available" documents
# (audit5 R-3).
_PLACEHOLDER_TITLE_RE = re.compile(r"<title[^>]*>\s*No content available\s*</title>", re.IGNORECASE)


def _reject_no_content_placeholder(html_text: str) -> None:
    """Reject ar5iv/arXiv "No content available" placeholder pages.

    PDF-only submissions have no HTML rendering; ar5iv then answers HTTP 200
    with a placeholder page. Treating it as paper content produces an empty
    document titled "No content available", so raise instead and let the
    caller surface a clear error.
    """
    if _PLACEHOLDER_TITLE_RE.search(html_text):
        raise NetworkError(
            "No HTML content available for this paper (ar5iv/arXiv returned a "
            "placeholder page). The paper was likely submitted as PDF-only and "
            "has no HTML rendering."
        )


async def fetch_arxiv_pdf(
    arxiv_id: str,
    output_path: Path,
    version: str | None = None,
    use_cache: bool = True,
) -> Path:
    """Download arXiv PDF and save to output path."""
    cache_path = cache_dir_for(arxiv_id, version) / "paper.pdf"

    if use_cache and is_cache_fresh(cache_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, cache_path, output_path)
        logger.debug(f"Using cached PDF for {arxiv_id}")
        return output_path

    pdf_url = get_settings().urls.arxiv_pdf_template.format(base_id=strip_version(arxiv_id))

    async def download_from(url: str) -> Path:
        await download_file_with_retries(
            url,
            cache_path,
            label=f"download PDF {url}",
            progress_label="Downloading PDF",
            not_found_error=lambda: NonRetryableNetworkError(f"PDF not found at {url}", status_code=404),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, cache_path, output_path)
        return output_path

    return await with_mirror_fallback(pdf_url, describe="PDF download", fetch=download_from)
