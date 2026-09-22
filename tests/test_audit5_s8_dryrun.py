"""--dry-run for ``convert`` and ``batch`` (audit5 S8 F1).

The plan (mode, output base dir, idempotency verdict) must be computed from
data available *before* ingestion — the exact paper directory name depends on
metadata only a real run can fetch, so dry-run reports the base dir and says
so. Contract under test:

- nothing is written: no output dir creation, no downloads, no
  ``download_manifest.json`` in batch mode;
- an already-completed identity is reported as "would skip", not "would
  convert";
- ``--force`` is reported as bypassing the idempotency check;
- batch carries the flag through ``merge_convert_params`` and records rows
  under a distinct ``"dry-run"`` status instead of writing the manifest.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from arxiv2md_beta.cli.runner.batch import merge_convert_params, run_batch_flow
from arxiv2md_beta.cli.runner.convert import run_convert_flow
from arxiv2md_beta.params import ConvertParams


def _params(**overrides: object) -> ConvertParams:
    defaults: dict[str, object] = {
        "input_text": "2401.12345",
        "parser": "html",
        "output": None,
        "source": "Arxiv",
        "short": None,
        "no_images": False,
        "remove_refs": False,
        "remove_inline_citations": False,
        "section_filter_mode": "include",
        "sections": None,
        "section": None,
        "include_tree": False,
        "dry_run": True,
    }
    defaults.update(overrides)
    return ConvertParams(**defaults)  # type: ignore[arg-type]


def _make_completed_dir(base: Path, identity: str) -> Path:
    d = base / "20240101-Arxiv-done"
    d.mkdir(parents=True)
    (d / ".arxiv2md-paper").write_text(identity + "\n", encoding="utf-8")
    (d / "paper.md").write_text("# done\n", encoding="utf-8")
    return d


def test_dry_run_writes_nothing_and_reports_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    params = _params(output=str(tmp_path / "out"))
    out = asyncio.run(run_convert_flow(params))
    assert out == tmp_path / "out"
    assert not (tmp_path / "out").exists(), "dry-run must not create the output directory"
    captured = capsys.readouterr().out
    assert "[dry-run]" in captured
    assert "would convert" in captured
    assert "2401.12345" in captured


def test_dry_run_reports_completed_as_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "out"
    _make_completed_dir(base, "2401.12345")
    params = _params(output=str(base))
    out = asyncio.run(run_convert_flow(params))
    assert out == base
    captured = capsys.readouterr().out
    assert "would skip" in captured
    assert "already converted" in captured


def test_dry_run_force_bypasses_idempotency_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "out"
    _make_completed_dir(base, "2401.12345")
    params = _params(output=str(base), force=True)
    asyncio.run(run_convert_flow(params))
    captured = capsys.readouterr().out
    assert "would convert" in captured
    assert "--force" in captured


def test_merge_convert_params_carries_dry_run(tmp_path: Path) -> None:
    template = _params(output=str(tmp_path))
    assert merge_convert_params(template, "2401.99999").dry_run is True
    assert replace(template, dry_run=False).dry_run is False


def test_batch_dry_run_writes_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "out"
    template = _params(output=str(base))
    results = asyncio.run(
        run_batch_flow(
            ["2401.12345", "2401.67890", "# comment", ""],
            params_template=template,
            max_concurrency=2,
            continue_on_error=True,
            delay_seconds=0.0,
        )
    )
    assert [r[3] for r in results] == ["dry-run", "dry-run", "skipped", "skipped"]
    assert base.exists() is False, "batch dry-run must not create the output directory"
    assert not (base / "download_manifest.json").exists()
    captured = capsys.readouterr().out
    assert captured.count("[dry-run]") >= 2
