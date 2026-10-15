"""Regression tests for the shared-console LiveError fix (batch concurrency).

Batch workers share one stderr :class:`~rich.console.Console`, and rich
allows a single live display per console: the second overlapping
``Progress`` used to raise ``LiveError: Only one live display may be active
at once``, which escaped the retry loop and was recorded as a bogus per-row
batch error (concurrency 1 → 0 failures, concurrency 3 → 2 LiveErrors).
Two layers fix it: batch forces ``no_progress`` for concurrency > 1, and
progress entry degrades to a disabled bar if a conflict still happens.
"""

from __future__ import annotations

import asyncio

import pytest

from arxiv2md_beta.utils.progress import async_byte_download_progress, iterable_task_progress


@pytest.mark.asyncio
async def test_concurrent_download_progress_never_raises_live_error() -> None:
    """Three overlapping download-progress contexts must all succeed.

    Reproduces the batch failure mode with the real package code: workers in
    one event loop entering ``async_byte_download_progress`` while another
    holds the console's live display. Entry must degrade to a disabled bar,
    never fail the download.
    """

    async def hold_bar() -> None:
        async with async_byte_download_progress("Downloading paper", 1024, disable=False) as advance:
            advance(16)
            # Hold the live display open so the next worker's entry collides.
            await asyncio.sleep(0.05)
            advance(32)

    results = await asyncio.gather(*(hold_bar() for _ in range(3)), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert failures == [], f"concurrent progress entry raised: {failures}"


@pytest.mark.asyncio
async def test_concurrent_iterable_progress_never_raises_live_error() -> None:
    """Same collision for the image-processing bar (second Live call site)."""

    async def hold_bar() -> None:
        with iterable_task_progress("Processing images", 4, disable=False) as advance:
            advance()
            await asyncio.sleep(0.05)
            advance()

    results = await asyncio.gather(*(hold_bar() for _ in range(3)), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert failures == [], f"concurrent progress entry raised: {failures}"


@pytest.mark.asyncio
async def test_progress_honours_disable_flag() -> None:
    """disable=True yields a working no-op advance without starting a live display."""

    async def run() -> None:
        async with async_byte_download_progress("Downloading", 10, disable=True) as advance:
            advance(5)

    await asyncio.gather(*(run() for _ in range(3)))
