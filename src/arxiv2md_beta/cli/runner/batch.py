"""Batch command runner."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from arxiv2md_beta.cli.runner.convert import find_completed_for_input, run_convert_flow
from arxiv2md_beta.exceptions import Arxiv2mdError, PdfFallbackCompleted
from arxiv2md_beta.output.layout import determine_output_dir
from arxiv2md_beta.output.manifest import (
    BatchManifestRecorder,
    batch_entry_details_from_manifest,
)
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


def normalize_batch_line(line: str) -> str:
    """Canonical form of a batch line for duplicate detection.

    arXiv IDs/URLs normalize to their ID (version kept, so ``<id>`` and
    ``<id>v2`` are different conversions); local paths to their resolved
    string; anything unparsable falls back to the stripped line. Never raises.
    """
    from arxiv2md_beta.query.parser import parse_arxiv_input

    stripped = line.strip()
    try:
        return parse_arxiv_input(stripped).arxiv_id
    except Exception:
        try:
            from pathlib import Path

            p = Path(stripped)
            if p.exists():
                return str(p.resolve())
        except (OSError, ValueError):
            pass
    return stripped


def run_batch_sync(
    lines: list[str],
    *,
    params_template: ConvertParams,
    max_concurrency: int,
    continue_on_error: bool,
    delay_seconds: float,
) -> list[tuple[str, str | None, str | None, str]]:
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
) -> list[tuple[str, str | None, str | None, str]]:
    """Run ``convert`` for each non-empty line.

    Returns tuples ``(input_line, error_or_none, paper_output_dir_or_none,
    status)`` where status is one of ``"ok"``, ``"error"``, ``"skipped"``
    (comment/blank), ``"skip-done"`` (idempotency hit; resume) or
    ``"duplicate"``. Comment lines and blank lines yield
    ``(line, None, None, "skipped")``.
    """
    async with async_timed_operation("run_batch_flow"):
        worker_count = max(1, max_concurrency)
        # --fail-fast: stop scheduling new conversions after the first failure
        # (in-flight ones finish); conversions already admitted past the
        # semaphore complete normally.
        stop_event = asyncio.Event() if not continue_on_error else None

        # Duplicate detection up front: repeats never enter the queue and
        # never consume a concurrency slot. Normalization is best-effort and
        # must not introduce new failures.
        first_seen: dict[str, int] = {}
        duplicates: dict[int, str] = {}
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key = normalize_batch_line(stripped)
            if key in first_seen:
                duplicates[index] = f"duplicate of line {first_seen[key] + 1}"
            else:
                first_seen[key] = index

        # Resume index: identities already converted under the base output dir
        # are skipped before ingestion. The per-item check inside
        # run_convert_flow stays authoritative; this only labels the row.
        force = params_template.force
        template = params_template
        base_output_dir = determine_output_dir(template.output)
        recorder = BatchManifestRecorder(base_output_dir)

        async def run_one(line: str, index: int) -> tuple[str, str | None, str | None, str]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                return (line, None, None, "skipped")
            if index in duplicates:
                return (line, None, None, duplicates[index])
            if stop_event is not None and stop_event.is_set():
                return (stripped, "skipped: an earlier conversion failed", None, "error")
            merged = merge_convert_params(template, stripped)
            if not force:
                done = find_completed_for_input(stripped, merged)
                if done is not None:
                    logger.info(f"Batch skip (already converted): {done}")
                    out_dir = str(done.resolve())
                    recorder.record(
                        input_line=stripped,
                        status="skipped_done",
                        output_dir=out_dir,
                        **batch_entry_details_from_manifest(done),
                    )
                    return (stripped, None, out_dir, "skip-done")
            if stop_event is not None and stop_event.is_set():
                return (stripped, "skipped: an earlier conversion failed", None, "error")
            if delay_seconds > 0 and index > 0:
                await asyncio.sleep(delay_seconds)
            try:
                out = await run_convert_flow(merged)
                out_dir = str(out.resolve())
                recorder.record(
                    input_line=stripped,
                    status="ok",
                    output_dir=out_dir,
                    **batch_entry_details_from_manifest(out),
                )
                return (stripped, None, out_dir, "ok")
            except PdfFallbackCompleted as exc:
                # Partial success: PDF ready, no Markdown. Not a hard failure.
                fb_dir = exc.paper_output_dir
                recorder.record(
                    input_line=stripped,
                    status="pdf_fallback",
                    output_dir=fb_dir,
                    error=str(exc),
                )
                return (stripped, None, fb_dir, "pdf-fallback")
            except (Arxiv2mdError, OSError) as exc:
                if stop_event is not None:
                    stop_event.set()
                recorder.record(input_line=stripped, status="error", error=str(exc))
                return (stripped, str(exc), None, "error")
            except Exception as exc:
                if stop_event is not None:
                    stop_event.set()
                recorder.record(input_line=stripped, status="error", error=f"{type(exc).__name__}: {exc}")
                return (stripped, f"{type(exc).__name__}: {exc}", None, "error")

        queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue(maxsize=worker_count)
        results: list[tuple[str, str | None, str | None, str] | None] = [None] * len(lines)

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    index, line = item
                    try:
                        results[index] = await run_one(line, index)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # A crashing run_one must never kill the worker:
                        # dead workers stop draining the queue and deadlock
                        # queue.join() below.
                        results[index] = (line.strip(), f"{type(exc).__name__}: {exc}", None, "error")
                        if stop_event is not None:
                            stop_event.set()
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
        for index, line in enumerate(lines):
            await queue.put((index, line))
        for _ in workers:
            await queue.put(None)
        await queue.join()
        await asyncio.gather(*workers)
        return [result for result in results if result is not None]
