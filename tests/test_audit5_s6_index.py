"""audit5 S6 PR6.2 (X1): shared idempotency index for batch/convert.

The per-row pre-check used to walk every sibling output directory — twice
(batch pre-check + the authoritative check inside run_convert_flow), O(n²)
stats at n=1000. One scan at batch start feeds both.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.cli.runner.batch import run_batch_flow
from arxiv2md_beta.output.layout import CompletedIdentityIndex


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
    )


def _completed_dir(base: Path, name: str, identity: str | None) -> Path:
    d = base / name
    d.mkdir(parents=True)
    if identity is not None:
        (d / ".arxiv2md-paper").write_text(identity + "\n", encoding="utf-8")
    (d / "paper.md").write_text("# done\n", encoding="utf-8")
    return d


def _empty_dir(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True)
    return d


class TestCompletedIdentityIndex:
    def test_exact_identity_hit(self, tmp_path: Path) -> None:
        done = _completed_dir(tmp_path, "a", "2501.11120")
        idx = CompletedIdentityIndex.build(tmp_path)
        assert idx.lookup("2501.11120") == done

    def test_markerless_and_unconverted_dirs_ignored(self, tmp_path: Path) -> None:
        _completed_dir(tmp_path, "converted", "2501.11120")
        _empty_dir(tmp_path, "no-marker")
        marker_only = tmp_path / "marker-only"  # marker but no markdown
        marker_only.mkdir()
        (marker_only / ".arxiv2md-paper").write_text("2501.11121\n", encoding="utf-8")
        idx = CompletedIdentityIndex.build(tmp_path)
        assert idx.lookup("2501.11121") is None  # marker but no markdown
        assert idx.lookup("9999.00001") is None  # unknown identity

    def test_empty_marker_is_wildcard(self, tmp_path: Path) -> None:
        """Legacy empty markers match any identity, as in the direct scan."""
        wild = _completed_dir(tmp_path, "legacy", "")
        idx = CompletedIdentityIndex.build(tmp_path)
        assert idx.lookup("anything") == wild

    def test_index_matches_direct_scan(self, tmp_path: Path) -> None:
        from arxiv2md_beta.output.layout import find_completed_output_dir

        _completed_dir(tmp_path, "a", "2501.11120")
        _completed_dir(tmp_path, "b", "2501.11121")
        idx = CompletedIdentityIndex.build(tmp_path)
        assert idx.lookup("2501.11120") == find_completed_output_dir(tmp_path, "2501.11120")
        assert idx.lookup("2501.11121") == find_completed_output_dir(tmp_path, "2501.11121")


@pytest.mark.asyncio
async def test_batch_builds_index_once_not_per_row(tmp_path: Path) -> None:
    """audit5 X1: three already-converted rows must not rescan the base dir 3×."""
    for i, ident in enumerate(("2501.00001", "2501.00002", "2501.00003")):
        _completed_dir(tmp_path, f"dir-{i}", ident)

    scans = {"n": 0}
    real = __import__("arxiv2md_beta.output.layout", fromlist=["find_completed_output_dir"]).find_completed_output_dir

    def counting(base_output_dir: Path, identity: str) -> Path | None:
        scans["n"] += 1
        return real(base_output_dir, identity)

    lines = ["2501.00001", "2501.00002", "2501.00003"]
    with (
        patch("arxiv2md_beta.cli.runner.convert.find_completed_output_dir", counting),
        patch("arxiv2md_beta.cli.runner.batch.run_convert_flow") as mock,
    ):
        out = await run_batch_flow(
            lines,
            params_template=_template(output=str(tmp_path)),
            max_concurrency=2,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    assert [r[3] for r in out] == ["skip-done"] * 3
    assert mock.await_count == 0
    # One shared index (built by its own single scan), zero per-row rescans.
    assert scans["n"] == 0, scans


@pytest.mark.asyncio
async def test_single_convert_uses_injected_index(tmp_path: Path) -> None:
    """The authoritative check inside _process_with reuses the shared index."""
    from dataclasses import replace

    from arxiv2md_beta.cli.runner.convert import run_convert_flow

    done = _completed_dir(tmp_path, "a", "2501.11120")
    idx = CompletedIdentityIndex.build(tmp_path)
    params = replace(_template(output=str(tmp_path)), input_text="2501.11120", completed_index=idx)

    scans = {"n": 0}
    real = __import__("arxiv2md_beta.output.layout", fromlist=["find_completed_output_dir"]).find_completed_output_dir

    def counting(base_output_dir: Path, identity: str) -> Path | None:
        scans["n"] += 1
        return real(base_output_dir, identity)

    with patch("arxiv2md_beta.cli.runner.convert.find_completed_output_dir", counting):
        result = await run_convert_flow(params)
    assert result == done
    assert scans["n"] == 0, scans
