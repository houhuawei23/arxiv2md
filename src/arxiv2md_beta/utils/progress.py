"""Rich-based progress (replacing tqdm) for downloads and iteration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from loguru import logger
from rich.console import Console
from rich.errors import LiveError
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

_console = Console(stderr=True)


def _start_or_disable(progress: Progress) -> Progress:
    """Start *progress*'s live display, degrading to a disabled bar on conflict.

    rich allows a single live display per :class:`Console`, and both helpers
    below share the module-level stderr console. Batch runs with concurrency
    > 1 disable progress upstream; this catches any unforeseen overlapping
    call site so progress stays pure display — a conflict must degrade to
    "no bar", never fail the conversion (a ``LiveError`` used to escape as a
    bogus per-row batch error).
    """
    try:
        progress.start()
    except LiveError as exc:
        logger.warning(f"Progress display conflict on the shared console; continuing without progress output ({exc})")
        progress.disable = True
    return progress


@asynccontextmanager
async def async_byte_download_progress(
    description: str,
    total: int | None,
    *,
    disable: bool,
) -> AsyncIterator[Callable[[int], None]]:
    """Advance by byte count while streaming a download."""
    if disable:

        def noop(_n: int) -> None:
            pass

        yield noop
        return

    eff_total = total if total and total > 0 else None
    progress = _start_or_disable(
        Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=_console,
            refresh_per_second=10,
        )
    )
    with progress:
        task = progress.add_task(description, total=eff_total)

        def advance(n: int) -> None:
            progress.update(task, advance=n)

        yield advance


@contextmanager
def iterable_task_progress(
    description: str,
    total: int,
    *,
    disable: bool,
) -> Iterator[Callable[[], None]]:
    """One advance() call per item (e.g. image processing)."""
    if disable or total <= 0:

        def noop() -> None:
            pass

        yield noop
        return

    progress = _start_or_disable(
        Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=_console,
            refresh_per_second=10,
        )
    )
    with progress:
        task = progress.add_task(description, total=total)

        def advance() -> None:
            progress.advance(task)

        yield advance
