"""Retry-semantics tests for the HTTP fetch layer (audit4 PR3.1 / A2 / A3)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import respx
from httpx import Response

from arxiv2md_beta.exceptions import NetworkError, NonRetryableNetworkError
from arxiv2md_beta.network import fetch as fetch_mod
from arxiv2md_beta.network.fetch import fetch_arxiv_html
from arxiv2md_beta.network.retry import compute_backoff

URL = "https://arxiv.org/html/2501.12345"


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace fetch's backoff sleeps with a recorder (keeps tests instant)."""
    sleeps: list[float] = []

    async def _record(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(fetch_mod.asyncio, "sleep", _record)
    return sleeps


@respx.mock
async def test_deterministic_4xx_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """audit4 A2: 403/410/451 are permanent — retrying only deepens a ban."""
    sleeps = _no_sleep(monkeypatch)
    route = respx.get(URL).mock(return_value=Response(403, text="blocked"))
    with pytest.raises(NonRetryableNetworkError) as excinfo:
        await fetch_arxiv_html(URL, arxiv_id="2501.12345", version=None, use_cache=False)
    assert excinfo.value.status_code == 403
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
async def test_410_gone_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    route = respx.get(URL).mock(return_value=Response(410, text="withdrawn"))
    with pytest.raises(NonRetryableNetworkError):
        await fetch_arxiv_html(URL, arxiv_id="2501.12345", version=None, use_cache=False)
    assert route.call_count == 1


@respx.mock
async def test_unlisted_4xx_error_carries_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exhausted-retries NetworkError must expose the status code."""
    _no_sleep(monkeypatch)
    respx.get(URL).mock(return_value=Response(409, text="conflict"))
    with pytest.raises(NetworkError) as excinfo:
        await fetch_arxiv_html(URL, arxiv_id="2501.12345", version=None, use_cache=False)
    assert excinfo.value.status_code == 409


@respx.mock
async def test_ar5iv_fallback_triggers_on_typed_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ar5iv fallback keys on exception type/status, not message text."""
    _no_sleep(monkeypatch)
    respx.get(URL).mock(return_value=Response(404, text="no html"))
    # The 404 also rates a mirror attempt (mirror_on_404) before ar5iv.
    respx.get("https://export.arxiv.org/html/2501.12345").mock(return_value=Response(404, text="no html"))
    ar5iv = respx.get("https://ar5iv.labs.arxiv.org/html/2501.12345").mock(
        return_value=Response(
            200, text="<html>ar5iv body</html>", headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    text = await fetch_arxiv_html(
        URL, arxiv_id="2501.12345", version=None, use_cache=False, ar5iv_url="https://ar5iv.labs.arxiv.org/html/2501.12345"
    )
    assert "ar5iv body" in text
    assert ar5iv.call_count == 1


def test_compute_backoff_is_jittered() -> None:
    """audit4 A3: backoff must not be deterministic across workers."""
    random.seed(1234)
    delays = [compute_backoff(3.0, 2) for _ in range(50)]
    assert all(0.5 * 3.0 * 4 <= d <= 1.5 * 3.0 * 4 for d in delays)
    assert len(set(delays)) > 1, "backoff has no jitter"


def test_compute_backoff_matches_exponential_shape() -> None:
    random.seed(0)
    random.uniform = lambda a, b: (a + b) / 2  # force the midpoint factor 1.0
    assert compute_backoff(2.0, 0) == 2.0
    assert compute_backoff(2.0, 1) == 4.0
    assert compute_backoff(2.0, 3) == 16.0


@respx.mock
async def test_pdf_download_survives_garbage_content_length(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed Content-Length header must not kill the PDF download."""
    _no_sleep(monkeypatch)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(fetch_mod, "_cache_dir_for", lambda arxiv_id, version: cache_dir)
    respx.get("https://arxiv.org/pdf/2501.12345.pdf").mock(
        return_value=Response(
            200,
            content=b"%PDF-1.4 fake",
            headers={"content-type": "application/pdf", "content-length": "not-a-number"},
        )
    )
    out = tmp_path / "out" / "paper.pdf"
    result = await fetch_mod.fetch_arxiv_pdf("2501.12345v1", out, version="v1", use_cache=False)
    assert result == out
    assert out.read_bytes().startswith(b"%PDF")
