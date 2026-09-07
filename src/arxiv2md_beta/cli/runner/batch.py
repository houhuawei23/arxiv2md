"""Batch command runner."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from arxiv2md_beta.cli.runner.convert import run_convert_flow
from arxiv2md_beta.exceptions import Arxiv2mdError
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.timing import async_timed_operation

if TYPE_CHECKING:
    from arxiv2md_beta.params import ConvertParams

logger = get_logger()


def merge_convert_params(template: ConvertParams, input_text: str) -> ConvertParams:
    """Build params for one batch line from a template.

    Uses :func:`dataclasses.replace` so every field of ``ConvertParams`` is
    carried over -- hand-listing fields previously dropped ``no_cache``,
    ``naming_scheme``, ``download_pdf`` and ``linked_citations`` in batch mode
    (silent feature loss).
    """
    return replace(template, input_text=input_text)


def run_batch_sync(
    lines: list[str],
    *,
    params_template: ConvertParams,
    max_concurrency: int,
    continue_on_error: bool,
    delay_seconds: float,
) -> list[tuple[str, str | None, str | None]]:
    """Run batch convert in a fresh event loop."""
    from arxiv2md_beta.network.http import run_async

    return run_async(
        run_batch_flow(
            lines,
            params_template=params_template,
            max_concurrency=max_concurrency,
            continue_on_error=continue_on_error,
            delay_seconds=delay_seconds,
        )
    )


async def run_batch_flow(
    lines: list[str],
    *,
    params_template: ConvertParams,
    max_concurrency: int,
    continue_on_error: bool,
    delay_seconds: float,
) -> list[tuple[str, str | None, str | None]]:
    """Run ``convert`` for each non-empty line.

    Returns tuples ``(input_line, error_or_none, paper_output_dir_or_none)``.
    Comment lines and blank lines yield ``(line, None, None)``.
    """
    async with async_timed_operation("run_batch_flow"):
        sem = asyncio.Semaphore(max(1, max_concurrency))
        # --fail-fast: stop scheduling new conversions after the first failure
        # (in-flight ones finish); conversions already admitted past the
        # semaphore complete normally.
        stop_event = asyncio.Event() if not continue_on_error else None

        async def run_one(line: str, index: int) -> tuple[str, str | None, str | None]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                return (line, None, None)
            if stop_event is not None and stop_event.is_set():
                return (stripped, "skipped: an earlier conversion failed", None)
            merged = merge_convert_params(params_template, stripped)
            async with sem:
                if stop_event is not None and stop_event.is_set():
                    return (stripped, "skipped: an earlier conversion failed", None)
                # Sleep inside the semaphore so concurrent tasks actually space
                # out their network bursts (outside it, everyone sleeps in
                # parallel and the delay has no rate-limiting effect).
                if delay_seconds > 0 and index > 0:
                    await asyncio.sleep(delay_seconds)
                try:
                    out = await run_convert_flow(merged)
                    return (stripped, None, str(out.resolve()))
                except (Arxiv2mdError, OSError) as exc:
                    if stop_event is not None:
                        stop_event.set()
                    return (stripped, str(exc), None)
                except Exception as exc:
                    # Unexpected errors (unwrapped httpx bugs, etc.) must not
                    # escape gather() and cancel sibling conversions.
                    if stop_event is not None:
                        stop_event.set()
                    return (stripped, f"{type(exc).__name__}: {exc}", None)

        tasks = [run_one(line, i) for i, line in enumerate(lines)]
        return list(await asyncio.gather(*tasks))
