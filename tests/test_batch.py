"""Tests for batch conversion flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.cli.runner import run_batch_flow


def _template() -> ConvertParams:
    return ConvertParams(
        input_text="",
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
        structured_output="none",
        emit_graph_csv=False,
    )


@pytest.mark.asyncio
async def test_run_batch_flow_skips_comments_and_blank() -> None:
    mock = AsyncMock(return_value=Path("/tmp/out"))
    lines = ["", "  ", "# comment", "2501.11120"]
    with patch("arxiv2md_beta.cli.runner.run_convert_flow", mock):
        out = await run_batch_flow(
            lines,
            params_template=_template(),
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
async def test_run_batch_flow_continue_on_error_collects() -> None:
    async def side_effect(params: ConvertParams) -> Path:
        if "bad" in params.input_text:
            raise ValueError("fail")
        return Path("/ok")

    lines = ["good1", "bad", "good2"]
    with patch("arxiv2md_beta.cli.runner.run_convert_flow", side_effect=side_effect):
        out = await run_batch_flow(
            lines,
            params_template=_template(),
            max_concurrency=2,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    assert len(out) == 3
    assert out[0][1] is None
    assert out[1][1] == "fail"
    assert out[2][1] is None


@pytest.mark.asyncio
async def test_run_batch_flow_fail_fast_stops() -> None:
    calls: list[str] = []

    async def side_effect(params: ConvertParams) -> Path:
        calls.append(params.input_text)
        if params.input_text == "bad":
            raise ValueError("fail")
        return Path("/ok")

    lines = ["good1", "bad", "good2"]
    with patch("arxiv2md_beta.cli.runner.run_convert_flow", side_effect=side_effect):
        out = await run_batch_flow(
            lines,
            params_template=_template(),
            max_concurrency=2,
            continue_on_error=False,
            delay_seconds=0.0,
        )
    assert len(out) == 2
    assert calls == ["good1", "bad"]
