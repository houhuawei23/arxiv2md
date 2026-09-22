"""Atomic write helpers: crash-safety properties (no torn files, no .part residue)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.utils.atomic_io import atomic_write_text, atomic_write_text_sync


def test_sync_write_success_no_part_residue(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write_text_sync(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert list(tmp_path.glob("*.part")) == []


def test_sync_failure_keeps_old_content_and_cleans_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old-content", encoding="utf-8")

    def _boom(self: Path, target: Path) -> Path:
        raise OSError("injected rename failure")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(OSError, match="injected rename failure"):
        atomic_write_text_sync(target, "new-content")

    # The previous full content survives; no .part sibling is left behind.
    assert target.read_text(encoding="utf-8") == "old-content"
    assert list(tmp_path.glob("*.part")) == []


async def test_async_write_success_no_part_residue(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    await atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert list(tmp_path.glob("*.part")) == []


async def test_async_failure_keeps_old_content_and_cleans_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old-content", encoding="utf-8")

    def _boom(self: Path, target: Path) -> Path:
        raise OSError("injected rename failure")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(OSError, match="injected rename failure"):
        await atomic_write_text(target, "new-content")

    assert target.read_text(encoding="utf-8") == "old-content"
    assert list(tmp_path.glob("*.part")) == []


async def test_async_write_creates_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "out.txt"
    await atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_sync_write_creates_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "out.txt"
    atomic_write_text_sync(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
