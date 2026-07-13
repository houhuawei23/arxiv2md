"""Performance monitoring and timing utilities."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from time import perf_counter

from loguru import logger


@contextmanager
def timed_operation(name: str) -> Iterator[None]:
    """Sync context manager that logs elapsed time for an operation."""
    start = perf_counter()
    try:
        yield
    finally:
        duration = perf_counter() - start
        logger.debug(f"{name} took {duration:.3f}s")


@asynccontextmanager
async def async_timed_operation(name: str) -> AsyncIterator[None]:
    """Async context manager that logs elapsed time for an operation."""
    start = perf_counter()
    try:
        yield
    finally:
        duration = perf_counter() - start
        logger.debug(f"{name} took {duration:.3f}s")
