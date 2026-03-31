"""Rich-based progress (replacing tqdm) for downloads and iteration."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Callable, Iterator

from rich.console import Console
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
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=_console,
        refresh_per_second=10,
    ) as progress:
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

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=_console,
        refresh_per_second=10,
    ) as progress:
        task = progress.add_task(description, total=total)

        def advance() -> None:
            progress.advance(task)

        yield advance
