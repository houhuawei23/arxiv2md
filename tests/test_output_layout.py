"""Output-directory claiming: concurrency safety and collision naming."""

from __future__ import annotations

import asyncio
from pathlib import Path

from arxiv2md_beta.output.layout import create_paper_output_dir


def test_same_identity_reuses_directory(tmp_path: Path) -> None:
    d1 = create_paper_output_dir(tmp_path, "20260101", "Same Title", identity="2501.11120")
    d2 = create_paper_output_dir(tmp_path, "20260101", "Same Title", identity="2501.11120")
    assert d1 == d2
    assert (d1 / ".arxiv2md-paper").read_text().strip() == "2501.11120"


def test_different_identity_gets_deterministic_suffix(tmp_path: Path) -> None:
    d1 = create_paper_output_dir(tmp_path, "20260101", "Same Title", identity="2501.11120v1")
    d2 = create_paper_output_dir(tmp_path, "20260101", "Same Title", identity="2501.11120v2")
    assert d1 != d2
    assert (d1 / ".arxiv2md-paper").read_text().strip() == "2501.11120v1"
    assert (d2 / ".arxiv2md-paper").read_text().strip() == "2501.11120v2"
    # Deterministic: another v2 call converges on the same suffixed directory.
    d2_again = create_paper_output_dir(tmp_path, "20260101", "Same Title", identity="2501.11120v2")
    assert d2_again == d2


def test_concurrent_versions_do_not_share_directory(tmp_path: Path) -> None:
    """Concurrent v1/v2 conversions must not share a directory.

    Regression: the old check-then-write marker handling let them overwrite
    each other's identity and outputs.
    """

    async def run() -> tuple[Path, Path]:
        return await asyncio.gather(
            asyncio.to_thread(create_paper_output_dir, tmp_path, "20260101", "Same Title", identity="2501.11120v1"),
            asyncio.to_thread(create_paper_output_dir, tmp_path, "20260101", "Same Title", identity="2501.11120v2"),
        )

    d1, d2 = asyncio.run(run())
    assert d1 != d2
    assert (d1 / ".arxiv2md-paper").read_text().strip() == "2501.11120v1"
    assert (d2 / ".arxiv2md-paper").read_text().strip() == "2501.11120v2"


def test_concurrent_same_identity_share_directory(tmp_path: Path) -> None:
    async def run() -> list[Path]:
        return await asyncio.gather(
            *(
                asyncio.to_thread(create_paper_output_dir, tmp_path, "20260101", "Same Title", identity="2501.11120")
                for _ in range(6)
            )
        )

    dirs = set(asyncio.run(run()))
    assert len(dirs) == 1


def test_empty_marker_directory_is_adopted(tmp_path: Path) -> None:
    orphan = tmp_path / "202601-Arxiv-Same-Title"
    orphan.mkdir()
    (orphan / ".arxiv2md-paper").write_text("", encoding="utf-8")
    d = create_paper_output_dir(tmp_path, "20260101", "Same Title", identity="2501.11120")
    assert d == orphan
    assert (d / ".arxiv2md-paper").read_text().strip() == "2501.11120"


def test_no_identity_creates_plain_directory(tmp_path: Path) -> None:
    d = create_paper_output_dir(tmp_path, "20260101", "Some Title")
    assert d.is_dir()
    assert not (d / ".arxiv2md-paper").exists()
