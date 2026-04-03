"""Performance monitoring and timing utilities."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from time import perf_counter
from typing import AsyncIterator, Iterator

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
