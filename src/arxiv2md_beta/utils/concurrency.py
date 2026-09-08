"""Loop-local concurrency limits shared across ingestion tasks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_semaphores: dict[tuple[int, str, int], asyncio.Semaphore] = {}


def _semaphore(name: str, limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = (id(loop), name, limit)
    semaphore = _semaphores.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, limit))
        _semaphores[key] = semaphore
    return semaphore


@asynccontextmanager
async def concurrency_slot(name: str, limit: int) -> AsyncIterator[None]:
    """Hold one named process-wide slot for the current event loop."""
    async with _semaphore(name, limit):
        yield
