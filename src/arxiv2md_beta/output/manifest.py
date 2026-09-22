"""Self-describing artifacts: per-paper and batch-level download manifests.

Born from a batch-download post-mortem where every consistency check between
the reference list and the converted outputs had to be done by hand:
``paper.manifest.json`` sits inside each paper directory, and a batch run
incrementally maintains ``download_manifest.json`` at the output root so a
crashed run still leaves a usable snapshot of everything completed so far.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arxiv2md_beta.output.markdown_utils import count_tokens
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()

MANIFEST_SCHEMA_VERSION = 1
PAPER_MANIFEST_FILENAME = "paper.manifest.json"
BATCH_MANIFEST_FILENAME = "download_manifest.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_paper_manifest(
    *,
    arxiv_id: str | None,
    title: str | None,
    submission_date: str | None,
    source_url: str | None,
    pdf_path: str | None,
    markdown_file: str | None,
    output_text: str,
    parser: str,
    naming_scheme: str,
    duration_seconds: float | None,
    status: str,
) -> dict[str, Any]:
    """Assemble the per-paper manifest payload (pure; no I/O)."""
    if not source_url and arxiv_id:
        source_url = f"https://arxiv.org/abs/{arxiv_id}"
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "arxiv_id": arxiv_id,
        "title": title,
        "submission_date": submission_date,
        "source_url": source_url,
        "pdf_path": pdf_path,
        "markdown_file": markdown_file,
        "content_chars": len(output_text),
        "content_bytes": len(output_text.encode("utf-8")),
        "token_estimate": count_tokens(output_text),
        "parser": parser,
        "naming_scheme": naming_scheme,
        "tool_version": _tool_version(),
        "generated_at": _utc_now_iso(),
        "duration_seconds": duration_seconds,
        "status": status,
    }


def _tool_version() -> str:
    from arxiv2md_beta import __version__

    return __version__


def write_paper_manifest(paper_output_dir: Path, manifest: dict[str, Any]) -> Path | None:
    """Write ``paper.manifest.json`` into the paper directory (best-effort)."""
    path = paper_output_dir / PAPER_MANIFEST_FILENAME
    try:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(f"Failed to write {PAPER_MANIFEST_FILENAME}: {exc}")
        return None
    return path


def read_paper_manifest(paper_output_dir: Path) -> dict[str, Any] | None:
    """Read a per-paper manifest; None when missing or unreadable."""
    path = paper_output_dir / PAPER_MANIFEST_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class BatchManifestRecorder:
    """Incrementally maintained ``download_manifest.json`` for a batch run.

    The whole file is rewritten (atomically) on a throttled cadence so the
    file on disk doubles as a crash snapshot — a re-run pre-fills ``ok``
    entries from it and only retries the rest. Full-file rewrites used to
    fire on *every* entry, which for n=1000 meant ~150MB of serialized JSON
    written synchronously on the event loop; flushes are now at most one per
    :attr:`min_flush_interval_s` (the first entry always lands immediately),
    batch end flushes explicitly, and :meth:`arecord` pushes the write to a
    worker thread.
    """

    def __init__(self, base_output_dir: Path, *, min_flush_interval_s: float = 2.0) -> None:
        self.path = base_output_dir / BATCH_MANIFEST_FILENAME
        self.min_flush_interval_s = min_flush_interval_s
        self.started_at = _utc_now_iso()
        self.entries: list[dict[str, Any]] = []
        # 0.0 makes the first record always flush (monotonic() is far ahead).
        self._last_flush_monotonic = 0.0
        self._flush_lock = threading.Lock()
        self._prefill_from_existing()

    def _prefill_from_existing(self) -> None:
        """Carry over completed entries from a previous (possibly crashed) run."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for entry in data.get("entries", []):
            if entry.get("status") == "ok":
                self.entries.append(entry)

    def record(
        self,
        *,
        input_line: str,
        status: str,
        output_dir: str | None = None,
        arxiv_id: str | None = None,
        title: str | None = None,
        duration_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        self.entries.append(
            {
                "input": input_line,
                "status": status,
                "output_dir": output_dir,
                "arxiv_id": arxiv_id,
                "title": title,
                "duration_seconds": duration_seconds,
                "error": error,
            }
        )
        self.maybe_flush()

    def maybe_flush(self) -> None:
        """Flush only when the throttle window has elapsed (no-op otherwise)."""
        if time.monotonic() - self._last_flush_monotonic < self.min_flush_interval_s:
            return
        self.flush()

    async def arecord(self, **kwargs: Any) -> None:
        """Async sibling of :meth:`record`: append on the loop, write off it.

        Batch worker tasks call this so the throttled full-file rewrite runs
        in the default executor instead of stalling every other worker.
        """
        self._append(kwargs)
        if time.monotonic() - self._last_flush_monotonic >= self.min_flush_interval_s:
            await asyncio.to_thread(self.flush)

    def _append(self, kwargs: dict[str, Any]) -> None:
        self.entries.append(
            {
                "input": kwargs["input_line"],
                "status": kwargs["status"],
                "output_dir": kwargs.get("output_dir"),
                "arxiv_id": kwargs.get("arxiv_id"),
                "title": kwargs.get("title"),
                "duration_seconds": kwargs.get("duration_seconds"),
                "error": kwargs.get("error"),
            }
        )

    def flush(self) -> None:
        totals: dict[str, int] = {}
        for entry in self.entries:
            key = entry.get("status", "unknown")
            totals[key] = totals.get(key, 0) + 1
        payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "started_at": self.started_at,
            "updated_at": _utc_now_iso(),
            "totals": totals,
            "entries": self.entries,
        }
        # Concurrent to_thread flushes must serialize: two interleaved
        # replaces could otherwise land an older payload last.
        with self._flush_lock:
            self._last_flush_monotonic = time.monotonic()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self.path.with_suffix(f".{uuid.uuid4().hex}.part")
                tmp_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                tmp_path.replace(self.path)
            except OSError as exc:
                logger.warning(f"Failed to write {BATCH_MANIFEST_FILENAME}: {exc}")


def batch_entry_details_from_manifest(output_dir: Path | None) -> dict[str, Any]:
    """Best-effort arxiv_id/title/duration from a finished paper's manifest."""
    if output_dir is None:
        return {}
    manifest = read_paper_manifest(Path(output_dir))
    if not manifest:
        return {}
    return {
        "arxiv_id": manifest.get("arxiv_id"),
        "title": manifest.get("title"),
        "duration_seconds": manifest.get("duration_seconds"),
        # Authoritative product status ("ok" / "pdf_only" / "allowed_stub");
        # batch used to label every no-exception row "ok", so a pdf_only
        # directory that holds just paper.yml looked fully converted
        # (audit5 G3-2).
        "manifest_status": manifest.get("status"),
    }
