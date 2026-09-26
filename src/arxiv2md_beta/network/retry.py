"""Shared best-effort HTTP retry loop with exponential backoff."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from loguru import logger

from arxiv2md_beta.exceptions import NetworkError, NonRetryableNetworkError
from arxiv2md_beta.network.http import acquire_rate_slot, get_http_client, http_request_slot
from arxiv2md_beta.settings import get_settings

T = TypeVar("T")


def compute_backoff(base_s: float, attempt: int, *, jitter: float = 0.5) -> float:
    """Exponential backoff with multiplicative jitter.

    Deterministic ``base * 2**attempt`` delays make every worker that hits a
    429 on the same edge re-send at the same instant, so the retry wave
    re-triggers the rate limit it is backing off from (audit4 A3). Full
    jitter would be ``random() * delay``; half-jitter (±50%) is enough and
    keeps delays predictable in tests.
    """
    return base_s * (2**attempt) * random.uniform(1.0 - jitter, 1.0 + jitter)


async def request_with_retries(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    label: str = "",
) -> httpx.Response | None:
    """GET *url* with exponential backoff on retryable statuses / transport errors.

    Best-effort semantics shared by the metadata fetchers (Crossref, OpenAlex,
    abs-page enrichment):

    - 2xx → the response is returned (caller parses it);
    - 404 → ``None`` (record not found is an expected outcome);
    - retryable status (429/5xx per settings) → backoff and retry;
    - other non-2xx or exhausted retries → ``None``.

    Never raises transport errors, so callers can treat ``None`` as "skip
    enrichment". Streaming downloads with strict failure semantics (HTML/PDF/
    TeX) keep their own loops.
    """
    s = get_settings()
    h = s.http
    retry_status = set(h.retry_status_codes)
    client = get_http_client()
    who = label or url
    last_err = "unknown error"

    for attempt in range(h.fetch_max_retries + 1):
        try:
            await acquire_rate_slot()
            async with http_request_slot():
                r = await client.get(url, headers=headers, timeout=timeout)
            if r.status_code == 404:
                return None
            if r.status_code in retry_status:
                last_err = f"HTTP {r.status_code}"
            elif r.is_success:
                return r
            else:
                # Non-retryable client error: retrying cannot help.
                logger.debug(f"{who} returned HTTP {r.status_code}; giving up")
                return None
        except httpx.RequestError as exc:
            last_err = str(exc)

        if attempt < h.fetch_max_retries:
            await asyncio.sleep(compute_backoff(h.fetch_backoff_s, attempt))

    logger.debug(f"{who} exhausted retries: {last_err}")
    return None


def network_error_exhausted(description: str) -> Callable[[Exception | None], NetworkError]:
    """Build the "retries exhausted" exception for :func:`strict_http_retry_loop`.

    Carries the last failure's ``status_code`` (if any) on the raised
    :class:`NetworkError` so mirror/fallback classification keeps working.
    """

    def build(last_exc: Exception | None) -> NetworkError:
        return NetworkError(f"{description}: {last_exc}", status_code=getattr(last_exc, "status_code", None))

    return build


async def strict_http_retry_loop(
    attempt: Callable[[int], Awaitable[T]],
    *,
    label: str,
    exhausted: Callable[[Exception | None], BaseException],
    retryable: tuple[type[Exception], ...] = (httpx.RequestError, httpx.HTTPStatusError, NetworkError),
) -> T:
    """Shared strict-retry skeleton for fetches that must raise on failure.

    The strict siblings of :func:`request_with_retries` (HTML fetch, PDF
    download, TeX source download) share the same boilerplate — attempt
    budget from ``http.fetch_max_retries``, :func:`compute_backoff` sleeps
    between attempts, immediate propagation of
    :class:`~arxiv2md_beta.exceptions.NonRetryableNetworkError`, and a final
    typed exception wrapping the last failure — but differ in the request
    itself (plain GET vs streaming) and in the status branching, which stays
    inside *attempt*. *attempt* either returns the result or raises:

    - a ``NonRetryableNetworkError`` (or anything not in *retryable*) →
      propagated at once;
    - anything in *retryable* → recorded as the last failure and retried.

    After the budget is spent, ``exhausted(last_exc)`` is raised.
    """
    s = get_settings()
    h = s.http
    last_exc: Exception | None = None

    for n in range(h.fetch_max_retries + 1):
        try:
            return await attempt(n)
        except NonRetryableNetworkError:
            raise
        except retryable as exc:
            last_exc = exc
            logger.debug(f"{label}: attempt {n + 1}/{h.fetch_max_retries + 1} failed: {exc}")

        if n < h.fetch_max_retries:
            await asyncio.sleep(compute_backoff(h.fetch_backoff_s, n))

    raise exhausted(last_exc)
