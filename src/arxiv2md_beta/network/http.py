"""Shared httpx.AsyncClient factory for connection reuse within a scope."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import TypeVar

import httpx

from arxiv2md_beta.settings import get_settings

_client: httpx.AsyncClient | None = None
# Event loop the shared client was created on. If a later caller runs on a
# different loop (e.g. a second asyncio.run in the same process, or a test),
# we rebuild so the client is never bound to a dead loop.
_client_loop: asyncio.AbstractEventLoop | None = None

T = TypeVar("T")


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
    kwargs: dict = {
        "timeout": timeout,
        "headers": headers,
        "follow_redirects": True,
        "limits": limits,
    }
    # Pick up proxy from environment (HTTP_PROXY / HTTPS_PROXY) or explicit override
    proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**kwargs)


def get_http_client() -> httpx.AsyncClient:
    """Return the module-level shared AsyncClient, creating it if needed.

    Rebuilds when the client is closed or was created on a different event loop
    (so re-entering ``asyncio.run`` in the same process does not raise
    "RuntimeError: ... attached to a different loop").
    """
    global _client, _client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    stale = _client_loop is not None and _client_loop is not current_loop
    if _client is None or _client.is_closed or stale:
        _client = _build_client()
        _client_loop = current_loop
    return _client


async def close_http_client() -> None:
    """Gracefully close the shared AsyncClient if open. Safe to call repeatedly.

    Runners should call this at the end of their async flow (via
    :func:`run_async`) so connections are released and the next ``asyncio.run``
    starts from a clean state.
    """
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None


async def _await_then_close(coro: Awaitable[T]) -> T:
    try:
        return await coro
    finally:
        await close_http_client()


def run_async(coro: Awaitable[T]) -> T:
    """Run *coro* in a fresh event loop, closing the shared HTTP client after.

    Wraps ``asyncio.run`` so the shared client's connection pool is gracefully
    shut down on the same loop that created it, instead of leaking until the
    process exits.
    """
    return asyncio.run(_await_then_close(coro))


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
