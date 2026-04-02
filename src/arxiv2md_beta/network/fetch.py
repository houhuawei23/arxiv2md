"""Fetch and cache arXiv HTML pages."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from arxiv2md_beta.exceptions import NetworkError
from arxiv2md_beta.network.http import async_http_client
from arxiv2md_beta.settings import get_settings
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
        return html_path.read_text(encoding="utf-8")

    try:
        html_text = await _fetch_with_retries(html_url)
        cache_dir.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_text, encoding="utf-8")
        return html_text
    except NetworkError as primary_error:
        if ar5iv_url and "does not have an HTML version" in str(primary_error):
            try:
                html_text = await _fetch_with_retries(ar5iv_url)
                cache_dir.mkdir(parents=True, exist_ok=True)
                html_path.write_text(html_text, encoding="utf-8")
                return html_text
            except Exception:
                pass
        raise primary_error


async def _fetch_with_retries(url: str) -> str:
    s = get_settings()
    h = s.http
    retry_status = set(h.retry_status_codes)
    last_exc: Exception | None = None

    async with async_http_client(timeout_s=h.fetch_timeout_s) as client:
        for attempt in range(h.fetch_max_retries + 1):
            try:
                response = await client.get(url)

                if response.status_code == 404:
                    raise NetworkError(
                        "This paper does not have an HTML version available on arXiv. "
                        "arxiv2md-beta requires papers to be available in HTML format. "
                        "Older papers may only be available as PDF."
                    )

                if response.status_code in retry_status:
                    last_exc = NetworkError(f"HTTP {response.status_code} from arXiv")
                else:
                    response.raise_for_status()
                    _ensure_html_response(response)
                    return response.text
            except (httpx.RequestError, httpx.HTTPStatusError, NetworkError) as exc:
                last_exc = exc

            if attempt < h.fetch_max_retries:
                backoff = h.fetch_backoff_s * (2**attempt)
                await asyncio.sleep(backoff)

        raise NetworkError(f"Failed to fetch HTML from {url}: {last_exc}")


def _ensure_html_response(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        raise ValueError(f"Unexpected content-type: {content_type}")


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
    h = s.http
    urls = s.urls
    retry_status = set(h.retry_status_codes)
    cache_dir = _cache_dir_for(arxiv_id, version)
    cache_path = cache_dir / "paper.pdf"

    if use_cache and _is_cache_fresh(cache_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_path, output_path)
        logger.debug(f"Using cached PDF for {arxiv_id}")
        return output_path

    base_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
    pdf_url = urls.arxiv_pdf_template.format(base_id=base_id)

    pdf_timeout = h.fetch_timeout_s * h.large_transfer_timeout_multiplier
    last_exc: Exception | None = None

    async with async_http_client(timeout_s=pdf_timeout) as client:
        for attempt in range(h.fetch_max_retries + 1):
            try:
                async with client.stream("GET", pdf_url) as response:
                    if response.status_code == 404:
                        raise NetworkError(f"PDF not found at {pdf_url}")

                    if response.status_code in retry_status:
                        last_exc = NetworkError(
                            f"HTTP {response.status_code} from arXiv"
                        )
                    else:
                        response.raise_for_status()

                        cache_dir.mkdir(parents=True, exist_ok=True)

                        disable_tqdm = s.images.disable_tqdm

                        total_size = int(response.headers.get("content-length", 0))
                        async with async_byte_download_progress(
                            "Downloading PDF",
                            total_size if total_size > 0 else None,
                            disable=disable_tqdm,
                        ) as advance:
                            with open(cache_path, "wb") as f:
                                async for chunk in response.aiter_bytes():
                                    f.write(chunk)
                                    advance(len(chunk))

                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(cache_path, output_path)
                        return output_path
            except (httpx.RequestError, httpx.HTTPStatusError, NetworkError) as exc:
                last_exc = exc

            if attempt < h.fetch_max_retries:
                backoff = h.fetch_backoff_s * (2**attempt)
                await asyncio.sleep(backoff)

        raise NetworkError(f"Failed to download PDF from {pdf_url}: {last_exc}")
