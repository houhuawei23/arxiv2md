"""Atomic file writes: ``.part`` sibling + ``os.replace`` rename.

A crash (OOM kill, Ctrl-C, disk-full) during an in-place ``open(path, "w")``
truncate-write leaves a torn file behind. Downstream consumers — most
critically the idempotency check in :mod:`arxiv2md_beta.output.layout` —
then mistake the half-written artifact for a completed conversion. Writing to
a uniquely named ``.part`` sibling and renaming makes the switch invisible:
readers see either the full old file or the full new file, never a hybrid.

``fsync`` defaults to off (rename ordering is already crash-safe against torn
*content*; the flag exists for callers that also need durability against
machine-level power loss, e.g. batch manifests).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import aiofiles


def _part_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")


def atomic_write_text_sync(
    path: Path,
    content: str,
    encoding: str = "utf-8",
    *,
    fsync: bool = False,
) -> None:
    """Synchronously write *content* to *path* via a ``.part`` sibling rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _part_path(path)
    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            f.write(content)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


async def atomic_write_text(
    path: Path,
    content: str,
    encoding: str = "utf-8",
    *,
    fsync: bool = False,
) -> None:
    """Async variant of :func:`atomic_write_text_sync`.

    The write goes through aiofiles (keeps the event loop responsive for
    large Markdown payloads); the rename is offloaded so ``os.replace`` never
    blocks the loop.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _part_path(path)
    try:
        async with aiofiles.open(tmp_path, "w", encoding=encoding) as f:
            await f.write(content)
            if fsync:
                await f.flush()
                os.fsync(f.fileno())
        await asyncio.to_thread(tmp_path.replace, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
