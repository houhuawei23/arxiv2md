"""Idempotency / resume: find_completed_output_dir + convert-runner skip + --force."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.cli.runner import run_convert_flow
from arxiv2md_beta.output.layout import find_completed_output_dir


def _params(tmp_path: Path, *, force: bool = False) -> ConvertParams:
    return ConvertParams(
        input_text="2501.11120",
        parser="html",
        output=str(tmp_path),
        source="Arxiv",
        short=None,
        no_images=True,
        remove_refs=False,
        remove_inline_citations=False,
        section_filter_mode="exclude",
        sections=None,
        section=None,
        include_tree=False,
        force=force,
    )


def _make_completed(tmp_path: Path, *, identity: str = "2501.11120") -> Path:
    out = tmp_path / "202501-Arxiv-Some-Paper"
    out.mkdir(parents=True)
    (out / ".arxiv2md-paper").write_text(identity + "\n", encoding="utf-8")
    (out / "paper.md").write_text("# real content\n", encoding="utf-8")
    return out


def test_find_completed_matches_identity(tmp_path: Path) -> None:
    done = _make_completed(tmp_path)
    assert find_completed_output_dir(tmp_path, "2501.11120") == done


def test_find_completed_rejects_different_identity(tmp_path: Path) -> None:
    _make_completed(tmp_path, identity="9999.99999")
    assert find_completed_output_dir(tmp_path, "2501.11120") is None


def test_find_completed_ignores_missing_marker(tmp_path: Path) -> None:
    # An unrelated directory with Markdown but no marker must never be adopted.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "readme.md").write_text("hello", encoding="utf-8")
    assert find_completed_output_dir(tmp_path, "2501.11120") is None


def test_find_completed_ignores_empty_markdown(tmp_path: Path) -> None:
    out = tmp_path / "202501-Arxiv-Some-Paper"
    out.mkdir(parents=True)
    (out / ".arxiv2md-paper").write_text("2501.11120\n", encoding="utf-8")
    (out / "paper.md").write_text("", encoding="utf-8")
    assert find_completed_output_dir(tmp_path, "2501.11120") is None


def test_find_completed_empty_marker_counts_as_match(tmp_path: Path) -> None:
    out = tmp_path / "202501-Arxiv-Some-Paper"
    out.mkdir(parents=True)
    (out / ".arxiv2md-paper").write_text("", encoding="utf-8")
    (out / "paper.md").write_text("x", encoding="utf-8")
    assert find_completed_output_dir(tmp_path, "2501.11120") == out


def test_find_completed_missing_base_dir(tmp_path: Path) -> None:
    assert find_completed_output_dir(tmp_path / "nope", "2501.11120") is None


@pytest.mark.asyncio
async def test_convert_flow_skips_completed(tmp_path: Path) -> None:
    _make_completed(tmp_path)
    ingest = AsyncMock()
    with patch("arxiv2md_beta.cli.runner.convert._ingest_arxiv_html", ingest):
        out = await run_convert_flow(_params(tmp_path))
    assert out == tmp_path / "202501-Arxiv-Some-Paper"
    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_convert_flow_force_reconverts(tmp_path: Path) -> None:
    _make_completed(tmp_path)
    # With --force the runner proceeds to ingestion; make the ingest step fail
    # fast with a recognizable error to prove it was reached.
    with (
        patch(
            "arxiv2md_beta.cli.runner.convert._ingest_arxiv_html",
            AsyncMock(side_effect=RuntimeError("reached ingestion")),
        ),
        pytest.raises(RuntimeError, match="reached ingestion"),
    ):
        await run_convert_flow(_params(tmp_path, force=True))
