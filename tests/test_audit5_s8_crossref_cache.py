"""Crossref disk cache (audit5 S8 F5 + R-12 negative cache).

Every ``linked-citations``/bibtex export used to re-hit the Crossref API for
the same DOIs on every run; a batch of N papers citing the same references
paid N× the requests (and rate-limit risk). ``fetch_crossref_metadata`` now
caches payloads under ``cache.dir/crossref/`` with the existing
``cache.ttl_seconds`` TTL. Misses are cached too (negative cache): a dead DOI
must not re-fetch on every run, only after the TTL expires.

Contract:

- second call with a warm cache performs no HTTP request;
- a failed lookup (None) is also cached, per R-12;
- expired entries are re-fetched;
- ``cache.ttl_seconds <= 0`` disables the disk cache entirely;
- ``use_cache=False`` bypasses reads and writes (the ``--no-cache`` path).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from arxiv2md_beta.network import crossref_api
from arxiv2md_beta.network.crossref_api import fetch_crossref_metadata
from arxiv2md_beta.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _cache_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The bundled test environment sets cache.ttl_seconds=0 (disk cache off);
    # these tests exercise the cache, so give it a real TTL.
    reset_settings_cache()
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("ARXIV2MD_BETA_CACHE__DIR", str(cache_dir))
    monkeypatch.setenv("ARXIV2MD_BETA_CACHE__TTL_SECONDS", "3600")
    return cache_dir


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _crossref_payload(title: str = "Cached Paper") -> dict:
    # Wrapped in Crossref's "message" envelope, which is what the parser reads.
    return {
        "status": "ok",
        "message": {
            "title": [title],
            "container-title": ["Journal of Tests"],
            "author": [{"given": "A.", "family": "Author"}],
            "published-print": {"date-parts": [[2020]]},
        },
    }


@pytest.fixture()
def requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _fake(url: str, **kwargs: object) -> _FakeResponse | None:
        calls.append(url)
        return _FakeResponse(_crossref_payload())

    monkeypatch.setattr(crossref_api, "request_with_retries", _fake)
    return calls


async def test_second_call_is_served_from_disk(tmp_path: Path, requests: list[str]) -> None:
    first = await fetch_crossref_metadata("10.1000/example")
    assert first is not None and first["title"] == "Cached Paper"
    second = await fetch_crossref_metadata("10.1000/example")
    assert second == first
    assert len(requests) == 1, "warm cache must not hit the network"
    files = list((tmp_path / "cache" / "crossref").glob("*.json"))
    assert len(files) == 1


async def test_doi_prefix_variants_share_one_entry(requests: list[str]) -> None:
    await fetch_crossref_metadata("10.1000/Example")
    await fetch_crossref_metadata("https://doi.org/10.1000/example")
    assert len(requests) == 1


async def test_negative_result_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _dead(url: str, **kwargs: object) -> None:
        calls.append(url)
        return None

    monkeypatch.setattr(crossref_api, "request_with_retries", _dead)
    assert await fetch_crossref_metadata("10.1000/dead") is None
    assert await fetch_crossref_metadata("10.1000/dead") is None
    assert len(calls) == 1, "negative cache: a dead DOI must not re-fetch within the TTL"
    payload = json.loads(next((tmp_path / "cache" / "crossref").glob("*.json")).read_text(encoding="utf-8"))
    assert payload["found"] is False


async def test_expired_entry_is_refetched(tmp_path: Path, requests: list[str]) -> None:
    await fetch_crossref_metadata("10.1000/example")
    cache_file = next((tmp_path / "cache" / "crossref").glob("*.json"))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["fetched_at"] = time.time() - 10_000_000
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    await fetch_crossref_metadata("10.1000/example")
    assert len(requests) == 2


async def test_ttl_le_zero_disables_disk_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requests) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ARXIV2MD_BETA_CACHE__TTL_SECONDS", "0")
    reset_settings_cache()
    await fetch_crossref_metadata("10.1000/example")
    await fetch_crossref_metadata("10.1000/example")
    assert len(requests) == 2
    assert not (tmp_path / "cache" / "crossref").exists()


async def test_use_cache_false_bypasses_reads_and_writes(tmp_path: Path, requests: list[str]) -> None:
    await fetch_crossref_metadata("10.1000/example", use_cache=False)
    await fetch_crossref_metadata("10.1000/example", use_cache=False)
    assert len(requests) == 2
    assert not (tmp_path / "cache" / "crossref").exists()
