"""Fetch and cache arXiv HTML pages."""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from aiofiles import open as aio_open
from loguru import logger

from arxiv2md_beta.exceptions import NetworkError, NonRetryableNetworkError
from arxiv2md_beta.network.http import acquire_rate_slot, get_http_client, http_request_slot
from arxiv2md_beta.network.mirror import mirror_worth_try, to_export_mirror
from arxiv2md_beta.network.retry import compute_backoff
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.atomic_io import atomic_write_text
from arxiv2md_beta.utils.progress import async_byte_download_progress


async def fetch_arxiv_html(
    html_url: str,
    *,
    arxiv_id: str,
    version: str | None,
    use_cache: bool = True,
    ar5iv_url: str | None = None,
) -> str:
    """Fetch arXiv HTML and cache it locally.

    Tries html_url first (arxiv.org), then falls back to ar5iv_url if 404.
    """
    cache_dir = _cache_dir_for(arxiv_id, version)
    html_path = cache_dir / "source.html"

    if use_cache and _is_cache_fresh(html_path):
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

    try:
        html_text = await _fetch_with_retries(html_url)
        _reject_no_content_placeholder(html_text)
        await atomic_write_text(html_path, html_text, encoding="utf-8")
        return html_text
    except NetworkError as primary_error:
        # Mirror fallback: the export origin serves from a different backend
        # and rate-limits independently, so a 404/429 here may not hold there.
        mirrored = to_export_mirror(html_url)
        if mirrored and mirror_worth_try(primary_error):
            try:
                html_text = await _fetch_with_retries(mirrored)
                _reject_no_content_placeholder(html_text)
                await atomic_write_text(html_path, html_text, encoding="utf-8")
                logger.info(f"Fetched HTML via export mirror: {mirrored}")
                return html_text
            except NetworkError as mirror_error:
                logger.warning(f"Export mirror fallback also failed: {mirror_error}")
        if ar5iv_url and isinstance(primary_error, NonRetryableNetworkError) and primary_error.status_code == 404:
            try:
                html_text = await _fetch_with_retries(ar5iv_url)
                _reject_no_content_placeholder(html_text)
                await atomic_write_text(html_path, html_text, encoding="utf-8")
                return html_text
            except (httpx.RequestError, httpx.HTTPStatusError, NetworkError, OSError) as fallback_error:
                logger.warning(f"ar5iv fallback also failed: {fallback_error}")
                raise primary_error from fallback_error
        raise primary_error


async def _fetch_with_retries(url: str) -> str:
    s = get_settings()
    h = s.http
    non_retryable = set(h.non_retryable_status_codes)
    last_exc: Exception | None = None

    client = get_http_client()
    for attempt in range(h.fetch_max_retries + 1):
        try:
            await acquire_rate_slot()
            async with http_request_slot():
                response = await client.get(url)

            if response.status_code == 404:
                # Deterministic: a second request will also 404. The mirror
                # and ar5iv fallbacks in fetch_arxiv_html rely on catching this.
                raise NonRetryableNetworkError(
                    "This paper does not have an HTML version available on arXiv. "
                    "arxiv2md-beta requires papers to be available in HTML format. "
                    "Older papers may only be available as PDF.",
                    status_code=404,
                )

            if response.status_code in non_retryable:
                # 403/410/451 are permanent: retrying cannot help and a 403
                # only deepens the ban (audit4 A2 — these used to burn the
                # full backoff budget, and the bare HTTPStatusError hid the
                # status from the mirror/fallback classification).
                raise NonRetryableNetworkError(
                    f"HTTP {response.status_code} from arXiv", status_code=response.status_code
                )

            if response.status_code >= 400:
                # Typed error instead of raise_for_status: the status code
                # must survive on the exception for mirror/fallback logic.
                # retry_status_codes carries no branch here — everything
                # short of the permanent set gets the backoff budget.
                last_exc = NetworkError(f"HTTP {response.status_code} from arXiv", status_code=response.status_code)
            else:
                _ensure_html_response(response)
                return response.text
        except NonRetryableNetworkError:
            raise
        except (httpx.RequestError, httpx.HTTPStatusError, NetworkError) as exc:
            last_exc = exc

        if attempt < h.fetch_max_retries:
            await asyncio.sleep(compute_backoff(h.fetch_backoff_s, attempt))

    status = getattr(last_exc, "status_code", None)
    raise NetworkError(f"Failed to fetch HTML from {url}: {last_exc}", status_code=status)


def _ensure_html_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        raise NetworkError(f"Unexpected content-type: {content_type}")


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


def _is_cache_fresh(html_path: Path) -> bool:
    s = get_settings()
    ttl = s.cache.ttl_seconds
    if not html_path.exists():
        return False
    if ttl <= 0:
        return True
    mtime = datetime.fromtimestamp(html_path.stat().st_mtime, tz=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - mtime).total_seconds()
    return age_seconds <= ttl


def _cache_dir_for(arxiv_id: str, version: str | None) -> Path:
    base = arxiv_id
    if version and arxiv_id.endswith(version):
        base = arxiv_id[: -len(version)]
    version_tag = version or "latest"
    key = f"{base}__{version_tag}".replace("/", "_")
    return get_settings().resolved_cache_path() / key


async def fetch_arxiv_pdf(
    arxiv_id: str,
    output_path: Path,
    version: str | None = None,
    use_cache: bool = True,
) -> Path:
    """Download arXiv PDF and save to output path."""
    s = get_settings()
    cache_dir = _cache_dir_for(arxiv_id, version)
    cache_path = cache_dir / "paper.pdf"

    if use_cache and _is_cache_fresh(cache_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, cache_path, output_path)
        logger.debug(f"Using cached PDF for {arxiv_id}")
        return output_path

    base_id = strip_version(arxiv_id)
    pdf_url = s.urls.arxiv_pdf_template.format(base_id=base_id)

    try:
        return await _download_pdf_from(pdf_url, cache_path=cache_path, output_path=output_path)
    except NetworkError as primary_error:
        # Mirror fallback: export.arxiv.org rate-limits independently and its
        # backend may hold files arxiv.org 404s on (and vice versa).
        mirrored = to_export_mirror(pdf_url)
        if mirrored and mirror_worth_try(primary_error):
            logger.warning(f"Retrying PDF download via export mirror: {mirrored}")
            try:
                return await _download_pdf_from(mirrored, cache_path=cache_path, output_path=output_path)
            except NetworkError as mirror_error:
                logger.warning(f"Export mirror PDF fallback also failed: {mirror_error}")
                raise primary_error from mirror_error
        raise


async def _download_pdf_from(pdf_url: str, *, cache_path: Path, output_path: Path) -> Path:
    """Retry-loop download of one PDF URL into the cache, then copy to output."""
    s = get_settings()
    h = s.http
    non_retryable = set(h.non_retryable_status_codes)
    pdf_timeout = h.fetch_timeout_s * h.large_transfer_timeout_multiplier
    last_exc: Exception | None = None

    client = get_http_client()
    non_retryable = set(h.non_retryable_status_codes)
    for attempt in range(h.fetch_max_retries + 1):
        try:
            await acquire_rate_slot()
            async with http_request_slot(), client.stream("GET", pdf_url, timeout=pdf_timeout) as response:
                if response.status_code == 404:
                    # Deterministic on this host: the mirror fallback in
                    # fetch_arxiv_pdf relies on catching this.
                    raise NonRetryableNetworkError(f"PDF not found at {pdf_url}", status_code=404)

                if response.status_code in non_retryable:
                    raise NonRetryableNetworkError(
                        f"HTTP {response.status_code} from arXiv", status_code=response.status_code
                    )

                if response.status_code >= 400:
                    # Retryable set or not: record and back off (the retryable
                    # check only matters for the 404/non-retryable branches above).
                    last_exc = NetworkError(f"HTTP {response.status_code} from arXiv", status_code=response.status_code)
                else:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)

                    disable_tqdm = s.images.disable_tqdm

                    # Malformed header (proxy noise, truncation) must not kill
                    # the download — fall back to the indeterminate progress bar.
                    try:
                        total_size = int(response.headers.get("content-length", 0))
                    except (TypeError, ValueError):
                        total_size = 0
                    total_size = max(0, total_size)
                    # Write to a temp sibling then rename so a concurrent
                    # conversion never sees (or overwrites) a half-written cache.
                    tmp_path = cache_path.with_name(f"{cache_path.name}.{uuid.uuid4().hex}.part")
                    try:
                        async with (
                            async_byte_download_progress(
                                "Downloading PDF",
                                total_size if total_size > 0 else None,
                                disable=disable_tqdm,
                            ) as advance,
                            aio_open(tmp_path, "wb") as f,
                        ):
                            async for chunk in response.aiter_bytes():
                                await f.write(chunk)
                                advance(len(chunk))
                        tmp_path.replace(cache_path)
                    finally:
                        tmp_path.unlink(missing_ok=True)

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, cache_path, output_path)
                    return output_path
        except NonRetryableNetworkError:
            raise
        except (httpx.RequestError, httpx.HTTPStatusError, NetworkError) as exc:
            last_exc = exc

        if attempt < h.fetch_max_retries:
            await asyncio.sleep(compute_backoff(h.fetch_backoff_s, attempt))

    status = getattr(last_exc, "status_code", None)
    raise NetworkError(f"Failed to download PDF from {pdf_url}: {last_exc}", status_code=status)
