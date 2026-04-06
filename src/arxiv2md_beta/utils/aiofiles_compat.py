"""Async file I/O compatibility helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


async def async_write_json(path: Path, data: Any, encoding: str = "utf-8") -> None:
    """Asynchronously write JSON *data* to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    async with aiofiles.open(path, "w", encoding=encoding) as f:
        await f.write(content)


async def async_read_json(path: Path, encoding: str = "utf-8") -> Any:
    """Asynchronously read JSON from *path*."""
    async with aiofiles.open(path, "r", encoding=encoding) as f:
        content = await f.read()
    return json.loads(content)
