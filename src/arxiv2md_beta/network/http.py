"""Shared httpx.AsyncClient factory for connection reuse within a scope."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from arxiv2md_beta.settings import get_settings

_client: httpx.AsyncClient | None = None


def _build_client(timeout_s: float | None = None) -> httpx.AsyncClient:
    """Construct a new AsyncClient from settings."""
    s = get_settings()
    h = s.http
    timeout = httpx.Timeout(timeout_s if timeout_s is not None else h.fetch_timeout_s)
    headers = {"User-Agent": h.user_agent}
    limits = httpx.Limits(
        max_connections=h.max_connections,
        max_keepalive_connections=h.max_keepalive_connections,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        follow_redirects=True,
        limits=limits,
    )


def get_http_client() -> httpx.AsyncClient:
    """Return the module-level shared AsyncClient, creating it if needed.

    The caller should not close this client directly; use ``async_http_client``
    for scoped management or let the module lifecycle handle cleanup.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = _build_client()
    return _client


@asynccontextmanager
async def async_http_client(
    *,
    timeout_s: float | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """One AsyncClient per ``async with`` block; retries reuse the same pool.

    If no custom timeout is requested, yields the shared module-level client.
    Otherwise creates a dedicated client with the requested timeout.
    """
    if timeout_s is None:
        yield get_http_client()
        return

    client = _build_client(timeout_s)
    try:
        yield client
    finally:
        await client.aclose()
