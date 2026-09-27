"""One download primitive for arXiv resources (HTML page, PDF, TeX tarball).

Everything that used to exist three/four times across ``fetch.py`` and
``tex_source.py`` lives here once:

- per-paper cache key (:func:`cache_dir_for`) and mtime-TTL freshness
  (:func:`is_cache_fresh`);
- arXiv status classification (:func:`raise_for_arxiv_status`): 404 → the
  caller's "not found" error (deterministic, so mirror fallbacks rely on
  catching it), permanent 4xx → NonRetryableNetworkError (a 403 only deepens
  the ban, audit4 A2), any other >=400 → retryable NetworkError carrying
  the status;
- streamed cache writes with progress bar and an atomic ``.part`` sibling
  rename (:func:`download_file_with_retries`), including the PDF-only sniff
  for ``/src/`` URLs (arXiv answers HTTP 200 with the rendered PDF);
- export-mirror fallback (:func:`with_mirror_fallback`) — export.arxiv.org
  rate-limits independently, so a 404/429 there may not hold here.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import httpx
from aiofiles import open as aio_open
from loguru import logger

from arxiv2md_beta.exceptions import NetworkError, NonRetryableNetworkError
from arxiv2md_beta.network.http import acquire_rate_slot, get_http_client, http_request_slot
from arxiv2md_beta.network.mirror import mirror_worth_try, to_export_mirror
from arxiv2md_beta.network.retry import network_error_exhausted, strict_http_retry_loop
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.progress import async_byte_download_progress

T = TypeVar("T")

# ── Cache ────────────────────────────────────────────────────────────


def cache_dir_for(arxiv_id: str, version: str | None) -> Path:
    """Per-paper cache directory ``<cache_root>/<base>__<version|latest>``."""
    version_tag = version or "latest"
    key = f"{strip_version(arxiv_id)}__{version_tag}".replace("/", "_")
    return get_settings().resolved_cache_path() / key


def is_cache_fresh(path: Path) -> bool:
    """True when *path* exists and its mtime is within ``cache.ttl_seconds``.

    ``ttl_seconds <= 0`` disables expiry: anything present counts as fresh.
    """
    if not path.exists():
        return False
    ttl = get_settings().cache.ttl_seconds
    if ttl <= 0:
        return True
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).total_seconds() <= ttl


# ── Status classification ────────────────────────────────────────────


def raise_for_arxiv_status(response: httpx.Response, *, not_found_error: Callable[[], NetworkError]) -> None:
    """Map an arXiv response status to typed errors; return when OK.

    *not_found_error* is built fresh per occurrence so each resource type
    supplies its own message/class (HTML "no HTML version", PDF "not found",
    TeX "likely PDF-only").
    """
    code = response.status_code
    if code == 404:
        raise not_found_error()
    if code in set(get_settings().http.non_retryable_status_codes):
        raise NonRetryableNetworkError(f"HTTP {code} from arXiv", status_code=code)
    if code >= 400:
        # Retryable: everything short of the permanent set gets the backoff
        # budget. The status must survive on the exception for the mirror
        # fallback's mirror_worth_try classification.
        raise NetworkError(f"HTTP {code} from arXiv", status_code=code)


# ── Downloads ────────────────────────────────────────────────────────


async def fetch_text_with_retries(url: str, *, label: str, not_found_error: Callable[[], NetworkError]) -> str:
    """GET *url* as text with the strict retry loop (HTML pages)."""
    client = get_http_client()

    async def attempt(_n: int) -> str:
        await acquire_rate_slot()
        async with http_request_slot():
            response = await client.get(url)
        raise_for_arxiv_status(response, not_found_error=not_found_error)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise NetworkError(f"Unexpected content-type: {content_type}")
        return response.text

    return await strict_http_retry_loop(
        attempt,
        label=label,
        exhausted=network_error_exhausted(f"Failed to fetch from {url}"),
    )


async def download_file_with_retries(
    url: str,
    cache_path: Path,
    *,
    label: str,
    progress_label: str,
    not_found_error: Callable[[], NetworkError],
    pdf_only_error: Callable[[], NetworkError] | None = None,
    exhausted: Callable[[Exception | None], BaseException] | None = None,
) -> Path:
    """Stream *url* into *cache_path* (atomic ``.part`` rename) with retries.

    ``pdf_only_error`` enables the PDF-only sniff: arXiv serves the rendered
    PDF from ``/src/`` with HTTP 200 for PDF-only submissions, detected via
    content-type and (for generic headers) magic bytes, so the bogus file
    never lands in the cache.
    """
    s = get_settings()
    timeout = s.http.fetch_timeout_s * s.http.large_transfer_timeout_multiplier
    client = get_http_client()

    async def attempt(_n: int) -> Path:
        await acquire_rate_slot()
        async with http_request_slot(), client.stream("GET", url, timeout=timeout) as response:
            raise_for_arxiv_status(response, not_found_error=not_found_error)

            if pdf_only_error is not None and "pdf" in response.headers.get("content-type", "").lower():
                raise pdf_only_error()

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            # Malformed header (proxy noise, truncation) must not kill the
            # download — fall back to an indeterminate progress bar.
            try:
                total_size = int(response.headers.get("content-length", 0))
            except (TypeError, ValueError):
                total_size = 0

            # Write to a temp sibling then rename so a concurrent conversion
            # never sees (or overwrites) a half-written cache.
            tmp_path = cache_path.with_name(f"{cache_path.name}.{uuid.uuid4().hex}.part")
            try:
                # The download stays inside the try: a mid-stream RequestError
                # used to leave the orphan .part behind (audit5 R-1).
                async with (
                    async_byte_download_progress(
                        progress_label,
                        total_size if total_size > 0 else None,
                        disable=s.images.disable_tqdm,
                    ) as advance,
                    aio_open(tmp_path, "wb") as f,
                ):
                    async for chunk in response.aiter_bytes():
                        await f.write(chunk)
                        advance(len(chunk))

                if pdf_only_error is not None and await asyncio.to_thread(_file_is_pdf, tmp_path):
                    raise pdf_only_error()
                tmp_path.replace(cache_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        return cache_path

    return await strict_http_retry_loop(
        attempt,
        label=label,
        exhausted=exhausted or network_error_exhausted(f"Failed to download from {url}"),
    )


async def with_mirror_fallback(url: str, *, describe: str, fetch: Callable[[str], Awaitable[T]]) -> T:
    """Run ``fetch(url)``; on a retry-worthy failure try the export mirror once.

    The mirror serves from a different backend and rate-limits independently,
    so its answer may differ from the origin's. A mirror failure re-raises
    the *primary* error (chained) — the origin diagnosis is the one users
    and fallback logic reason about.
    """
    try:
        return await fetch(url)
    except NetworkError as primary_error:
        mirrored = to_export_mirror(url)
        if not (mirrored and mirror_worth_try(primary_error)):
            raise
        logger.warning(f"Retrying {describe} via export mirror: {mirrored}")
        try:
            return await fetch(mirrored)
        except NetworkError as mirror_error:
            logger.warning(f"Export mirror {describe} fallback also failed: {mirror_error}")
            raise primary_error from mirror_error


def _file_is_pdf(path: Path) -> bool:
    """Sniff the 5-byte PDF magic without reading the whole file (audit5 R-2)."""
    with open(path, "rb") as f:
        return f.read(5) == b"%PDF-"
