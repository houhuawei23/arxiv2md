"""Loop-local concurrency limits shared across ingestion tasks."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# Keyed by the loop object itself (weakly): id(loop) keys are reused after a
# loop is garbage-collected, so a new loop silently inherited the previous
# loop's exhausted semaphores; the dict also grew without bound (audit5 R-7).
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, int], asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


def _semaphore(name: str, limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    per_loop = _semaphores.get(loop)
    if per_loop is None:
        per_loop = {}
        _semaphores[loop] = per_loop
    key = (name, limit)
    semaphore = per_loop.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, limit))
        per_loop[key] = semaphore
    return semaphore


@asynccontextmanager
async def concurrency_slot(name: str, limit: int) -> AsyncIterator[None]:
    """Hold one named process-wide slot for the current event loop."""
    async with _semaphore(name, limit):
        yield
