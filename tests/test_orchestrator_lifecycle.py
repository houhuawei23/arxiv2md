"""Task-lifecycle tests for the ingestion orchestrator (audit4 PR3.2 / A1).

The first try-block in ``IngestionOrchestrator.run`` used to catch only
``CancelledError``, so a non-cancel exception from the HTML/metadata phase
(cache OSError, ParseError, ...) leaked the in-flight TeX download task: it
kept running a full retry cycle (occupying rate-limit slots in batch mode)
and finally surfaced as "Task exception was never retrieved".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from arxiv2md_beta.ingestion.orchestrator import IngestionOrchestrator
from tests.test_ingest_degrade import _orchestrator_with_output_dir


@pytest.mark.asyncio
async def test_html_phase_failure_reaps_tex_task(tmp_path, monkeypatch) -> None:
    """A non-cancel failure in the HTML phase must cancel AND await tex_task."""
    orch: IngestionOrchestrator = _orchestrator_with_output_dir(tmp_path)
    spawned: list[asyncio.Task] = []

    def fake_start() -> asyncio.Task:
        task = asyncio.create_task(asyncio.sleep(60))
        spawned.append(task)
        return task

    async def fail_html() -> None:
        raise OSError("injected: disk full while writing cache")

    monkeypatch.setattr(orch, "_start_tex_fetch", fake_start)
    monkeypatch.setattr(orch, "_fetch_html_and_metadata", fail_html)

    with pytest.raises(OSError, match="injected"):
        await orch.run()

    await asyncio.sleep(0)  # let any cancellation callback settle
    assert spawned, "tex task was not spawned"
    assert spawned[0].done() and spawned[0].cancelled(), "tex_task leaked (still pending)"


@pytest.mark.asyncio
async def test_rate_lock_rebuilt_when_loop_changed(monkeypatch) -> None:
    """A rate lock bound to a dead loop must be rebuilt, not reused.

    Library callers (and some tests) drive a second ``asyncio.run`` without
    ``close_http_client``; acquiring the stale lock then raised
    "bound to a different event loop".
    """
    import threading

    from arxiv2md_beta.network import http as http_mod

    monkeypatch.setattr(
        http_mod,
        "get_settings",
        lambda: SimpleNamespace(http=SimpleNamespace(max_requests_per_second=100.0)),
    )

    holder: dict[str, asyncio.Lock] = {}

    def _bind_lock_in_fresh_loop() -> None:
        async def bind() -> None:
            holder["lock"] = asyncio.Lock()
            async with holder["lock"]:  # bind it to this (soon dead) loop
                pass

        asyncio.run(bind())

    thread = threading.Thread(target=_bind_lock_in_fresh_loop)
    thread.start()
    thread.join()
    stale_lock = holder["lock"]

    monkeypatch.setattr(http_mod, "_rate_lock", stale_lock)
    monkeypatch.setattr(http_mod, "_rate_next_slot", 0.0)

    await http_mod.acquire_rate_slot()  # must not raise
    assert http_mod._rate_lock is not stale_lock, "stale lock was reused"
