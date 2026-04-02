"""Shared httpx.AsyncClient factory for connection reuse within a scope."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from arxiv2md_beta.settings import get_settings


@asynccontextmanager
async def async_http_client(
    *,
    timeout_s: float | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """One AsyncClient per ``async with`` block; retries reuse the same pool."""
    s = get_settings()
    h = s.http
    timeout = httpx.Timeout(timeout_s if timeout_s is not None else h.fetch_timeout_s)
    headers = {"User-Agent": h.user_agent}
    limits = httpx.Limits(
        max_connections=h.max_connections,
        max_keepalive_connections=h.max_keepalive_connections,
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        follow_redirects=True,
        limits=limits,
    ) as client:
        yield client
