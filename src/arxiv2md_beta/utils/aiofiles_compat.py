"""Async file I/O compatibility helpers."""

from __future__ import annotations

from pathlib import Path

import aiofiles


async def async_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Asynchronously write *content* to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding=encoding) as f:
        await f.write(content)


async def async_read_text(path: Path, encoding: str = "utf-8") -> str:
    """Asynchronously read text from *path*."""
    async with aiofiles.open(path, "r", encoding=encoding) as f:
        return await f.read()
