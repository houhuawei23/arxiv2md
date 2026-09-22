"""Tests for per-paper and batch manifests."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from arxiv2md_beta.output.manifest import (
    BATCH_MANIFEST_FILENAME,
    PAPER_MANIFEST_FILENAME,
    BatchManifestRecorder,
    build_paper_manifest,
    read_paper_manifest,
    write_paper_manifest,
)


def test_build_paper_manifest_fields() -> None:
    m = build_paper_manifest(
        arxiv_id="2501.11120",
        title="A Paper",
        submission_date="20250120",
        source_url=None,
        pdf_path="/out/x.pdf",
        markdown_file="paper.md",
        output_text="hello world " * 10,
        parser="html",
        naming_scheme="arxiv-ym",
        duration_seconds=1.5,
        status="ok",
    )
    assert m["schema_version"] == 1
    assert m["source_url"] == "https://arxiv.org/abs/2501.11120"  # derived
    assert m["content_chars"] == len("hello world " * 10)
    assert m["status"] == "ok"
    assert m["token_estimate"] is None or m["token_estimate"] > 0


def test_write_and_read_paper_manifest(tmp_path: Path) -> None:
    m = build_paper_manifest(
        arxiv_id="2501.11120",
        title="T",
        submission_date=None,
        source_url=None,
        pdf_path=None,
        markdown_file="paper.md",
        output_text="text",
        parser="html",
        naming_scheme="arxiv-ym",
        duration_seconds=None,
        status="ok",
    )
    path = write_paper_manifest(tmp_path, m)
    assert path == tmp_path / PAPER_MANIFEST_FILENAME
    assert read_paper_manifest(tmp_path) == m


def test_batch_recorder_incremental_writes(tmp_path: Path) -> None:
    # S6 PR6.1: immediate per-entry flushing is now opt-in via a zero
    # throttle window; the default throttles (see the tests below).
    rec = BatchManifestRecorder(tmp_path, min_flush_interval_s=0.0)
    rec.record(input_line="2501.11120", status="ok", output_dir="/a")
    # Incremental: file already readable after the first entry.
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["totals"] == {"ok": 1}
    rec.record(input_line="9999.99999", status="error", error="boom")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["totals"] == {"ok": 1, "error": 1}
    assert len(data["entries"]) == 2


def test_batch_recorder_prefills_ok_from_previous_run(tmp_path: Path) -> None:
    rec1 = BatchManifestRecorder(tmp_path)
    rec1.record(input_line="2501.11120", status="ok", output_dir="/a")
    rec1.record(input_line="2501.99999", status="error", error="boom")

    # A fresh run carries over ok entries; failed ones are retried (absent).
    rec2 = BatchManifestRecorder(tmp_path)
    assert [e["input"] for e in rec2.entries] == ["2501.11120"]
    rec2.record(input_line="2501.99999", status="ok", output_dir="/b")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["totals"] == {"ok": 2}


def test_batch_recorder_throttles_flushes(tmp_path: Path) -> None:
    """audit5 X1 sibling (S6 PR6.1): record() must not rewrite the file per entry.

    n=1000 entries meant ~150MB of full-file rewrites; flushes are now
    throttled to ``min_flush_interval_s`` with an explicit ``flush()`` at
    batch end. The first record still flushes immediately so a batch that
    dies after one paper keeps its crash snapshot.
    """
    rec = BatchManifestRecorder(tmp_path, min_flush_interval_s=60.0)
    rec.record(input_line="2501.11120", status="ok", output_dir="/a")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["totals"] == {"ok": 1}
    rec.record(input_line="9999.99999", status="error", error="boom")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    # Inside the throttle window: only the first entry is on disk so far.
    assert data["totals"] == {"ok": 1}
    rec.flush()
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["totals"] == {"ok": 1, "error": 1}
    assert len(data["entries"]) == 2


def test_batch_recorder_maybe_flush_after_window(tmp_path: Path) -> None:
    """Once the throttle window elapses, record() flushes again on its own."""
    rec = BatchManifestRecorder(tmp_path, min_flush_interval_s=60.0)
    rec.record(input_line="2501.11120", status="ok", output_dir="/a")
    rec.record(input_line="2501.11121", status="ok", output_dir="/b")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert len(data["entries"]) == 1
    rec._last_flush_monotonic = time.monotonic() - 61.0  # window elapsed
    rec.record(input_line="2501.11122", status="ok", output_dir="/c")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert len(data["entries"]) == 3


@pytest.mark.asyncio
async def test_batch_recorder_arecord_offloads_flush(tmp_path: Path) -> None:
    """``arecord`` appends on the loop and pushes the throttled write to a thread."""
    rec = BatchManifestRecorder(tmp_path, min_flush_interval_s=60.0)
    await rec.arecord(input_line="2501.11120", status="ok", output_dir="/a")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert data["totals"] == {"ok": 1}
    await rec.arecord(input_line="2501.11121", status="ok", output_dir="/b")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert len(data["entries"]) == 1  # inside window
    rec._last_flush_monotonic = time.monotonic() - 61.0
    await rec.arecord(input_line="2501.11122", status="ok", output_dir="/c")
    data = json.loads((tmp_path / BATCH_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert len(data["entries"]) == 3  # due flush went through the executor


def test_build_paper_manifest_reuses_token_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """audit5 X3: manifest accepts the finalize pass's token count verbatim."""

    def boom(text: str) -> int | None:
        raise AssertionError("count_tokens must not be called when token_count is given")

    monkeypatch.setattr("arxiv2md_beta.output.manifest.count_tokens", boom)
    m = build_paper_manifest(
        arxiv_id="2501.11120",
        title="T",
        submission_date=None,
        source_url=None,
        pdf_path=None,
        markdown_file="paper.md",
        output_text="text",
        parser="html",
        naming_scheme="arxiv-ym",
        duration_seconds=None,
        status="ok",
        token_count=4242,
    )
    assert m["token_estimate"] == 4242
