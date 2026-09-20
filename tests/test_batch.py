"""Tests for batch conversion flow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.cli.runner import run_batch_flow
from arxiv2md_beta.cli.runner.batch import merge_convert_params
from arxiv2md_beta.exceptions import UserInputError


def _template(output: str | None = None) -> ConvertParams:
    return ConvertParams(
        input_text="",
        parser="html",
        output=output,
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


def test_merge_convert_params_preserves_all_fields() -> None:
    """Regression: batch must carry every ConvertParams field, not a hand-picked subset.

    Previously ``merge_convert_params`` hand-listed 16 of 21 non-input fields and
    silently dropped ``no_cache``/``download_pdf``/``linked_citations``, so
    ``batch --no-cache`` used the cache, ``--linked-citations`` produced plain
    ``[N]``, etc.
    """
    template = ConvertParams(
        input_text="",
        parser="html",
        output=None,
        source="Arxiv",
        short=None,
        no_images=True,
        remove_refs=True,
        remove_inline_citations=True,
        section_filter_mode="exclude",
        sections="References",
        section=["Abstract"],
        include_tree=True,
        emit_result_json=True,
        structured_output="full",
        emit_graph_csv=True,
        no_cache=True,
        download_pdf=False,
        linked_citations=True,
    )

    merged = merge_convert_params(template, input_text="2501.11120")

    # Every non-input field must equal the template's value.
    for field_name in template.__dataclass_fields__:
        if field_name == "input_text":
            assert getattr(merged, field_name) == "2501.11120"
            continue
        assert getattr(merged, field_name) == getattr(template, field_name), (
            f"merge_convert_params dropped field {field_name!r}"
        )


@pytest.mark.asyncio
async def test_run_batch_flow_skips_comments_and_blank(tmp_path: Path) -> None:
    mock = AsyncMock(return_value=Path("/tmp/out"))
    lines = ["", "  ", "# comment", "2501.11120"]
    with patch("arxiv2md_beta.cli.runner.batch.run_convert_flow", mock):
        out = await run_batch_flow(
            lines,
            params_template=_template(output=str(tmp_path)),
            max_concurrency=2,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    assert len(out) == 4
    assert out[0][2] is None and out[0][1] is None
    assert out[1][2] is None
    assert out[2][2] is None
    assert out[3][1] is None and out[3][2] is not None
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_run_batch_flow_continue_on_error_collects(tmp_path: Path) -> None:
    async def side_effect(params: ConvertParams) -> Path:
        if "bad" in params.input_text:
            raise UserInputError("fail")
        return Path("/ok")

    lines = ["good1", "bad", "good2"]
    with patch("arxiv2md_beta.cli.runner.batch.run_convert_flow", side_effect=side_effect):
        out = await run_batch_flow(
            lines,
            params_template=_template(output=str(tmp_path)),
            max_concurrency=2,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    assert len(out) == 3
    assert out[0][1] is None
    assert out[1][1] == "fail"
    assert out[2][1] is None


@pytest.mark.asyncio
async def test_run_batch_flow_fail_fast_stops(tmp_path: Path) -> None:
    """--fail-fast keeps concurrency: no serial for-loop.

    After the first failure, unstarted lines are skipped (reported with a
    skip marker) and no exception escapes gather(). With max_concurrency=1
    the ordering is deterministic: good1, bad, then good2 is skipped.
    """
    calls: list[str] = []

    async def side_effect(params: ConvertParams) -> Path:
        calls.append(params.input_text)
        if params.input_text == "good1":
            await asyncio.sleep(0.05)  # hold the single slot
        if params.input_text == "bad":
            raise UserInputError("fail")
        return Path("/ok")

    lines = ["good1", "bad", "good2"]
    with patch("arxiv2md_beta.cli.runner.batch.run_convert_flow", side_effect=side_effect):
        out = await run_batch_flow(
            lines,
            params_template=_template(output=str(tmp_path)),
            max_concurrency=1,
            continue_on_error=False,
            delay_seconds=0.0,
        )
    assert len(out) == 3  # every line yields a result
    assert calls == ["good1", "bad"]  # good2 never started
    assert out[0] == ("good1", None, "/ok", "ok")
    assert out[1] == ("bad", "fail", None, "error")
    assert "skipped" in out[2][1]


@pytest.mark.asyncio
async def test_run_batch_flow_fail_fast_skips_before_admission(tmp_path: Path) -> None:
    """Lines queued behind a failure are skipped without touching the semaphore."""
    calls: list[str] = []

    async def side_effect(params: ConvertParams) -> Path:
        calls.append(params.input_text)
        if params.input_text == "bad":
            raise UserInputError("fail")
        return Path("/ok")

    lines = ["bad", "a", "b", "c"]
    with patch("arxiv2md_beta.cli.runner.batch.run_convert_flow", side_effect=side_effect):
        out = await run_batch_flow(
            lines,
            params_template=_template(output=str(tmp_path)),
            max_concurrency=4,
            continue_on_error=False,
            delay_seconds=0.0,
        )
    assert calls == ["bad"]
    assert out[0] == ("bad", "fail", None, "error")
    for _line, err, _path, _status in out[1:]:
        assert "skipped" in (err or "")


@pytest.mark.asyncio
async def test_run_batch_flow_resume_skips_completed(tmp_path: Path) -> None:
    """Re-running the same file skips papers with a completed output (resume)."""
    out_dir = tmp_path / "202501-Arxiv-Some-Paper"
    out_dir.mkdir(parents=True)
    (out_dir / ".arxiv2md-paper").write_text("2501.11120\n", encoding="utf-8")
    (out_dir / "paper.md").write_text("# done\n", encoding="utf-8")

    mock = AsyncMock(return_value=Path("/tmp/out"))
    template = _template(output=str(tmp_path))

    with patch("arxiv2md_beta.cli.runner.batch.run_convert_flow", mock):
        out = await run_batch_flow(
            ["2501.11120"],
            params_template=template,
            max_concurrency=1,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    assert mock.await_count == 0
    assert out[0][3] == "skip-done"
    assert out[0][2] == str(out_dir.resolve())


@pytest.mark.asyncio
async def test_run_batch_flow_dedupes_repeated_ids(tmp_path: Path) -> None:
    """Repeated arXiv IDs (raw or URL form) are converted once."""
    mock = AsyncMock(return_value=Path("/tmp/out"))
    lines = [
        "2501.11120",
        "2501.11120",
        "https://arxiv.org/abs/2501.11120",
        "2501.99999",
    ]
    with patch("arxiv2md_beta.cli.runner.batch.run_convert_flow", mock):
        out = await run_batch_flow(
            lines,
            params_template=_template(output=str(tmp_path)),
            max_concurrency=2,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    assert mock.await_count == 2  # 2501.11120 (once) + 2501.99999
    assert out[0][3] == "ok"
    assert "duplicate of line 1" in out[1][3]
    assert "duplicate of line 1" in out[2][3]
    assert out[3][3] == "ok"
