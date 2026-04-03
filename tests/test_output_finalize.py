"""Tests for cli/output_finalize helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from arxiv2md_beta.cli.output_finalize import (
    emit_result_json_line,
    format_output,
    resolve_paper_output_dir,
    write_result_json_sidecar,
    write_split_markdown_sidecars,
)
from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.schemas import IngestionResult


def test_format_output_with_tree() -> None:
    out = format_output("S", "T", "C", include_tree=True)
    assert "S" in out and "T" in out and "C" in out


def test_format_output_without_tree() -> None:
    out = format_output("S", "T", "C", include_tree=False)
    assert "T" not in out.split("\n\n")[1] if "\n\n" in out else True
    assert "S" in out and "C" in out


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
    await write_split_markdown_sidecars(tmp_path, "paper.md", r)
    assert (tmp_path / "paper-References.md").read_text() == "refs"
    assert (tmp_path / "paper-Appendix.md").read_text() == "app"


@pytest.mark.asyncio
async def test_write_result_json_sidecar(tmp_path: Path) -> None:
    pdir = tmp_path / "p"
    pdir.mkdir()
    await write_result_json_sidecar(
        tmp_path,
        pdir,
        result_key="1234.5678",
        arxiv_id="1234.5678v1",
        structured=None,
    )
    files = list(tmp_path.glob(".arxiv2md-result-*.json"))
    assert len(files) == 1


def test_emit_result_json_line_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    p = ConvertParams(
        input_text="x",
        parser="html",
        output=None,
        source="Arxiv",
        short=None,
        no_images=True,
        remove_refs=False,
        remove_toc=False,
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
        remove_toc=False,
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
            result_key="1234.5678",
            arxiv_id_for_sidecar="1234.5678",
            fallback_md_stem="1234.5678",
            pdf_fetch=("1234.5678", None),
            log_local_success=False,
        )
    assert out.is_dir()
    mds = list(out.glob("*.md"))
    assert mds and mds[0].read_text(encoding="utf-8")
