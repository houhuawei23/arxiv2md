"""Batch command runner."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from arxiv2md_beta.cli.runner.base import merge_convert_params
from arxiv2md_beta.cli.runner.convert import run_convert_flow
from arxiv2md_beta.exceptions import Arxiv2mdError
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.metrics import async_timed_operation

if TYPE_CHECKING:
    from arxiv2md_beta.cli.params import ConvertParams

logger = get_logger()


def run_batch_sync(
    lines: list[str],
    *,
    params_template: ConvertParams,
    max_concurrency: int,
    continue_on_error: bool,
    delay_seconds: float,
) -> list[tuple[str, str | None, str | None]]:
    """Run batch convert in a fresh event loop."""
    return asyncio.run(
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

        async def run_one(line: str, index: int) -> tuple[str, str | None, str | None]:
            if delay_seconds > 0 and index > 0:
                await asyncio.sleep(delay_seconds)
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                return (line, None, None)
            merged = merge_convert_params(params_template, stripped)
            async with sem:
                try:
                    out = await run_convert_flow(merged)
                    return (stripped, None, str(out.resolve()))
                except (ValueError, OSError, RuntimeError, Arxiv2mdError) as exc:
                    return (stripped, str(exc), None)

        if continue_on_error:
            tasks = [run_one(line, i) for i, line in enumerate(lines)]
            return list(await asyncio.gather(*tasks))

        results: list[tuple[str, str | None, str | None]] = []
        for i, line in enumerate(lines):
            item = await run_one(line, i)
            results.append(item)
            err = item[1]
            if err is not None:
                break
        return results
