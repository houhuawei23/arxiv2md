"""Output-directory claiming: concurrency safety and collision naming."""

from __future__ import annotations

import asyncio
from pathlib import Path

from arxiv2md_beta.output.layout import create_paper_output_dir, determine_output_dir, find_completed_output_dir


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


# ── Idempotency adoption (find_completed_output_dir) ──────────────────────


def _make_output(base: Path, name: str, identity: str, files: dict[str, str]) -> Path:
    d = base / name
    d.mkdir()
    (d / ".arxiv2md-paper").write_text(identity + "\n", encoding="utf-8")
    for fname, content in files.items():
        (d / fname).write_text(content, encoding="utf-8")
    return d


def test_completed_dir_with_nonempty_markdown_is_adopted(tmp_path: Path) -> None:
    _make_output(tmp_path, "202601-Arxiv-Paper", "2501.11120", {"paper.md": "# Real content\n\n" * 10})
    assert find_completed_output_dir(tmp_path, "2501.11120") is not None


def test_truncated_write_leaving_only_part_file_is_not_adopted(tmp_path: Path) -> None:
    """Regression (A5): a `.part` residue must never satisfy the idempotency check."""
    _make_output(tmp_path, "202601-Arxiv-Paper", "2501.11120", {"paper.md.abc123.part": "# partial wri"})
    assert find_completed_output_dir(tmp_path, "2501.11120") is None


def test_part_residue_alongside_real_markdown_still_adopts(tmp_path: Path) -> None:
    _make_output(
        tmp_path,
        "202601-Arxiv-Paper",
        "2501.11120",
        {"paper.md": "# Real content\n" * 5, "paper.md.deadbeef.part": "# partial"},
    )
    assert find_completed_output_dir(tmp_path, "2501.11120") is not None


def test_empty_markdown_is_not_adopted(tmp_path: Path) -> None:
    _make_output(tmp_path, "202601-Arxiv-Paper", "2501.11120", {"paper.md": ""})
    assert find_completed_output_dir(tmp_path, "2501.11120") is None


# ── determine_output_dir ──────────────────────────────────────────────────


def test_determine_output_dir_expands_user_home() -> None:
    """Regression (A6): a quoted ``~/...`` must not create a literal ``~`` directory."""
    resolved = determine_output_dir("~/arxiv2md-out")
    assert resolved == Path.home() / "arxiv2md-out"


def test_cjk_long_title_truncated_by_utf8_bytes(tmp_path: Path) -> None:
    """audit4 S5.1: CJK titles must truncate on UTF-8 byte boundaries."""
    from arxiv2md_beta.output.layout import create_paper_output_dir, sanitize_title_for_filesystem

    title = "基于多尺度时序建模的长文本自动摘要方法研究" * 20  # ~880 CJK chars
    safe = sanitize_title_for_filesystem(title)
    assert len(safe.encode("utf-8")) <= 220, f"{len(safe.encode('utf-8'))} bytes"

    d = create_paper_output_dir(tmp_path, "20260101", title)  # must not raise
    assert d.is_dir()
    assert len(d.name.encode("utf-8")) <= 255
