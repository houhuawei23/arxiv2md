"""Tests for cli/output_finalize helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from arxiv2md_beta.cli.output_finalize import (
    emit_result_json_line,
    format_output,
    resolve_paper_output_dir,
    write_split_markdown_sidecars,
)
from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.output.layout import build_output_basename
from arxiv2md_beta.schemas import IngestionResult


def test_format_output_with_tree() -> None:
    out = format_output("S", "T", "C", include_tree=True)
    assert "S" in out and "T" in out and "C" in out


def test_format_output_without_tree() -> None:
    out = format_output("S", "T", "C", include_tree=False)
    assert "T" not in out.split("\n\n")[1] if "\n\n" in out else True
    assert "S" in out and "C" in out


def test_build_output_basename_arxiv_ym() -> None:
    # YYYYMM truncates full YYYYMMDD submission date to 6-digit year-month.
    got = build_output_basename(
        "20200101",
        "Hello World Title Here",
        source="Arxiv",
        short=None,
        naming_scheme="arxiv-ym",
    )
    assert got == "202001-Arxiv-Hello-World-Title-Here"


def test_build_output_basename_arxiv_ym_with_short() -> None:
    got = build_output_basename(
        "20260603",
        "LimiX",
        source="Arxiv",
        short="2M",
        naming_scheme="arxiv-ym",
    )
    assert got == "202606-Arxiv-2M-LimiX"


def test_resolve_paper_output_dir_from_metadata_str(tmp_path: Path) -> None:
    sub = tmp_path / "out"
    sub.mkdir()
    meta = {"paper_output_dir": str(sub), "submission_date": "2020-01-01", "title": "T"}
    got = resolve_paper_output_dir(meta, tmp_path, source="Arxiv", short=None)
    assert got == sub


@pytest.mark.asyncio
async def test_write_split_markdown_sidecars(tmp_path: Path) -> None:
    r = IngestionResult(
        summary="s",
        sections_tree="",
        content="c",
        content_references="refs",
        content_appendix="app",
    )
    await write_split_markdown_sidecars(tmp_path, "paper.md", r, naming_scheme="classic")
    assert (tmp_path / "paper-References.md").read_text() == "refs"
    assert (tmp_path / "paper-Appendix.md").read_text() == "app"


@pytest.mark.asyncio
async def test_write_split_markdown_sidecars_arxiv_ym_fixed(tmp_path: Path) -> None:
    r = IngestionResult(
        summary="s",
        sections_tree="",
        content="c",
        content_references="refs",
        content_appendix="app",
    )
    await write_split_markdown_sidecars(tmp_path, "paper.md", r, naming_scheme="arxiv-ym")
    assert (tmp_path / "References.md").read_text() == "refs"
    assert (tmp_path / "Appendix.md").read_text() == "app"


def test_emit_result_json_line_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    p = ConvertParams(
        input_text="x",
        parser="html",
        output=None,
        source="Arxiv",
        short=None,
        no_images=True,
        remove_refs=False,
        remove_inline_citations=False,
        section_filter_mode="exclude",
        sections=None,
        section=None,
        include_tree=False,
        emit_result_json=False,
    )
    emit_result_json_line(Path("/tmp"), params=p, structured=None)
    assert "ARXIV2MD_RESULT_JSON" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_finalize_convert_output_writes_md(tmp_path: Path) -> None:
    from arxiv2md_beta.cli.output_finalize import finalize_convert_output

    result = IngestionResult(summary="Sum", sections_tree="", content="Body")
    meta = {
        "submission_date": "20200101",
        "title": "Hello World Title Here",
        "paper_output_dir": None,
    }
    params = ConvertParams(
        input_text="1234.5678",
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
        emit_result_json=False,
        structured_output="none",
        emit_graph_csv=False,
    )
    with patch(
        "arxiv2md_beta.cli.output_finalize.fetch_arxiv_pdf",
        new=AsyncMock(return_value=None),
    ):
        out = await finalize_convert_output(
            result=result,
            metadata=meta,
            params=params,
            base_output_dir=tmp_path,
            fallback_md_stem="1234.5678",
            pdf_fetch=("1234.5678", None),
            log_local_success=False,
        )
    assert out.is_dir()
    mds = list(out.glob("*.md"))
    assert mds and mds[0].read_text(encoding="utf-8")
