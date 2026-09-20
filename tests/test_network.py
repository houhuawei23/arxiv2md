"""Comprehensive tests for network module."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from arxiv2md_beta.exceptions import NetworkError, UserInputError
from arxiv2md_beta.network.fetch import _cache_dir_for, _is_cache_fresh, fetch_arxiv_html
from arxiv2md_beta.network.http import _build_client, async_http_client, get_http_client
from arxiv2md_beta.query.parser import parse_arxiv_input


class TestHttpClient:
    """Tests for HTTP client functionality."""

    def test_get_http_client_singleton(self):
        """Test that get_http_client returns a singleton."""
        client1 = get_http_client()
        client2 = get_http_client()
        assert client1 is client2

    def test_build_client(self):
        """Test client building with settings."""
        client = _build_client(timeout_s=60.0)
        assert client is not None
        assert client.timeout.read == 60.0

    @pytest.mark.asyncio
    async def test_async_http_client_context_manager(self):
        """Test async context manager for HTTP client."""
        async with async_http_client() as client:
            assert client is not None


class TestFetchArxivHtml:
    """Tests for fetching arXiv HTML."""

    @pytest.mark.asyncio
    async def test_fetch_with_cache_miss(self, tmp_path, monkeypatch):
        """Test fetching when not in cache."""
        # Mock settings to use temp cache dir
        from arxiv2md_beta import settings as settings_module

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: type(
                "obj",
                (object,),
                {
                    "resolved_cache_path": lambda: tmp_path,
                    "cache": type("cache", (), {"ttl_seconds": 86400})(),
                },
            )(),
        )

        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(
                    200,
                    text="<html>Test content</html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )

            html = await fetch_arxiv_html(
                "https://arxiv.org/html/2501.12345",
                arxiv_id="2501.12345",
                version=None,
                use_cache=False,  # Disable cache to ensure HTTP request is made
            )
            assert "Test content" in html

    @pytest.mark.asyncio
    async def test_fetch_404_raises_network_error(self, tmp_path, monkeypatch):
        """Test that 404 raises NetworkError."""
        from arxiv2md_beta import settings as settings_module
        from arxiv2md_beta.network import fetch as fetch_module

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: type(
                "obj",
                (object,),
                {
                    "resolved_cache_path": lambda: tmp_path,
                    "cache": type("cache", (), {"ttl_seconds": 86400})(),
                },
            )(),
        )
        # Minimal fake settings above have no mirror config; disable the
        # mirror so this test stays focused on the 404 → NetworkError path.
        monkeypatch.setattr(fetch_module, "to_export_mirror", lambda *a, **k: None)

        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )

            with pytest.raises(NetworkError) as exc_info:
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,  # Disable cache to ensure HTTP request is made
                )
            assert "does not have an HTML version" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_retry_on_server_error(self, tmp_path, monkeypatch):
        """Test retry on 5xx errors."""
        from arxiv2md_beta import settings as settings_module

        call_count = 0

        def mock_get_settings():
            class MockHttp:
                fetch_max_retries = 2
                fetch_backoff_s = 0.01  # Fast for testing
                fetch_timeout_s = 10.0
                retry_status_codes = [500, 502, 503, 504]
                user_agent = "test"

            class MockCache:
                ttl_seconds = 86400

            class MockSettings:
                def resolved_cache_path(self):
                    return tmp_path

                http = MockHttp()
                cache = MockCache()

            return MockSettings()

        monkeypatch.setattr(settings_module, "get_settings", mock_get_settings)

        with respx.mock:

            def side_effect(request):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return Response(503, text="Service unavailable")
                return Response(200, text="<html>Success</html>")

            respx.get("https://arxiv.org/html/2501.12345").side_effect = side_effect

            # This should eventually succeed after retries
            # Note: Actual retry logic is in _fetch_with_retries


class TestParseArxivInput:
    """Tests for parsing arXiv input."""

    def test_parse_abs_url(self):
        """Parse from abs URL."""
        url = "http://arxiv.org/abs/2501.12345"
        result = parse_arxiv_input(url)
        assert result.arxiv_id == "2501.12345"

    def test_parse_html_url(self):
        """Parse from HTML URL."""
        url = "http://arxiv.org/html/2501.12345"
        result = parse_arxiv_input(url)
        assert result.arxiv_id == "2501.12345"

    def test_parse_pdf_url(self):
        """Parse from PDF URL."""
        url = "http://arxiv.org/pdf/2501.12345.pdf"
        result = parse_arxiv_input(url)
        assert result.arxiv_id == "2501.12345"

    def test_parse_with_version(self):
        """Parse ID with version."""
        url = "http://arxiv.org/abs/2501.12345v2"
        result = parse_arxiv_input(url)
        assert result.arxiv_id == "2501.12345v2"
        assert result.version == "v2"

    def test_parse_invalid_url_raises(self):
        """Parse from invalid URL raises UserInputError."""
        url = "http://example.com/not-arxiv"
        with pytest.raises(UserInputError):
            parse_arxiv_input(url)


class TestCacheHelpers:
    """Tests for cache helper functions."""

    def test_cache_dir_for(self, tmp_path, monkeypatch):
        """Test cache directory generation."""
        from arxiv2md_beta import settings as settings_module

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: type("obj", (object,), {"resolved_cache_path": lambda: tmp_path})(),
        )

        cache_dir = _cache_dir_for("2501.12345", None)
        assert "2501.12345" in str(cache_dir)
        assert "__latest" in str(cache_dir)

    def test_cache_dir_for_with_version(self, tmp_path, monkeypatch):
        """Test cache directory generation with version."""
        from arxiv2md_beta import settings as settings_module

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: type("obj", (object,), {"resolved_cache_path": lambda: tmp_path})(),
        )

        cache_dir = _cache_dir_for("2501.12345", "v2")
        assert "2501.12345" in str(cache_dir)
        assert "__v2" in str(cache_dir)

    def test_is_cache_fresh(self, tmp_path, monkeypatch):
        """Test cache freshness check."""
        from arxiv2md_beta import settings as settings_module

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: type("obj", (object,), {"cache": type("cache", (), {"ttl_seconds": 3600})()})(),
        )

        cache_file = tmp_path / "test.html"
        cache_file.write_text("content")

        # Should be fresh with positive TTL
        assert _is_cache_fresh(cache_file) is True

        # Update settings to ttl=0
        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: type("obj", (object,), {"cache": type("cache", (), {"ttl_seconds": 0})()})(),
        )
        # Should be fresh with zero TTL (infinite cache)
        assert _is_cache_fresh(cache_file) is True

        # Should not be fresh if file doesn't exist
        assert _is_cache_fresh(tmp_path / "nonexistent.html") is False


def test_close_http_client_resets_singleton():
    """close_http_client must aclose the shared client and reset the singleton."""
    import asyncio

    from arxiv2md_beta.network import http as httpmod

    async def _check():
        c1 = httpmod.get_http_client()
        assert not c1.is_closed
        await httpmod.close_http_client()
        # After close, get_http_client must build a fresh client.
        c2 = httpmod.get_http_client()
        assert c2 is not c1
        assert not c2.is_closed
        await httpmod.close_http_client()

    asyncio.run(_check())


def test_get_http_client_rebuilds_across_loops():
    """Regression: a second asyncio.run must not reuse a client bound to a dead loop."""
    import asyncio

    from arxiv2md_beta.network import http as httpmod

    async def _grab():
        return httpmod.get_http_client()

    c1 = asyncio.run(_grab())
    # c1 is now bound to a closed loop. A second asyncio.run must rebuild.
    c2 = asyncio.run(_grab())
    assert c2 is not c1, "shared HTTP client was not rebuilt across event loops"

    async def _cleanup():
        await httpmod.close_http_client()

    asyncio.run(_cleanup())


def test_rate_lock_reset_across_loops(monkeypatch):
    """close_http_client must reset the rate-lock along with the client.

    Regression: the lock stayed bound to the first loop, so the next
    asyncio.run crashed with "attached to a different event loop" as soon as
    max_requests_per_second was enabled.
    """
    import asyncio

    from arxiv2md_beta.network import http as httpmod

    base = httpmod.get_settings()
    rate_limited = base.model_copy(update={"http": base.http.model_copy(update={"max_requests_per_second": 50.0})})
    monkeypatch.setattr(httpmod, "get_settings", lambda: rate_limited)

    async def _use_rate_slot():
        await httpmod.acquire_rate_slot()

    asyncio.run(_use_rate_slot())
    asyncio.run(_use_rate_slot())  # crashed before the lock reset

    async def _cleanup():
        await httpmod.close_http_client()

    asyncio.run(_cleanup())


class TestFetch404NoRetry:
    """404 is deterministic: exactly one HTTP request, no retry backoff."""

    def _mock_settings(self, tmp_path, monkeypatch):
        """Point fetch's cache dir at tmp_path.

        fetch imports get_settings at module level, so patching the settings
        module is not enough.
        """
        from arxiv2md_beta.network import fetch as fetch_module

        monkeypatch.setattr(
            fetch_module,
            "_cache_dir_for",
            lambda arxiv_id, version: tmp_path / f"{arxiv_id}__{version or 'latest'}",
        )

    @pytest.mark.asyncio
    async def test_html_404_makes_exactly_one_request(self, tmp_path, monkeypatch):
        """404 is not retried on the same host; the export mirror gets one try."""
        self._mock_settings(tmp_path, monkeypatch)
        with respx.mock:
            route = respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            route_mirror = respx.get("https://export.arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            with pytest.raises(NetworkError) as exc_info:
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,
                )
            assert "does not have an HTML version" in str(exc_info.value)
            assert route.call_count == 1
            assert route_mirror.call_count == 1

    @pytest.mark.asyncio
    async def test_html_404_falls_back_to_ar5iv_single_attempt_each(self, tmp_path, monkeypatch):
        """arXiv, export mirror, and ar5iv each get exactly one request; error chained."""
        self._mock_settings(tmp_path, monkeypatch)
        with respx.mock:
            route_main = respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            route_mirror = respx.get("https://export.arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            route_ar5iv = respx.get("https://ar5iv.labs.arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            with pytest.raises(NetworkError) as exc_info:
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,
                    ar5iv_url="https://ar5iv.labs.arxiv.org/html/2501.12345",
                )
            assert "does not have an HTML version" in str(exc_info.value)
            assert route_main.call_count == 1
            assert route_mirror.call_count == 1
            assert route_ar5iv.call_count == 1
            assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_html_404_no_part_file_left_behind(self, tmp_path, monkeypatch):
        """A failed fetch leaves no .part cache artifact."""
        self._mock_settings(tmp_path, monkeypatch)
        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            respx.get("https://export.arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            with pytest.raises(NetworkError):
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,
                )
        assert list(tmp_path.rglob("*.part")) == []


class TestAtomicCacheWrite:
    """HTML cache writes are atomic (.part + rename)."""

    _mock_settings = TestFetch404NoRetry._mock_settings

    @pytest.mark.asyncio
    async def test_successful_write_leaves_no_part_file(self, tmp_path, monkeypatch):
        self._mock_settings(tmp_path, monkeypatch)
        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(
                    200,
                    text="<html>Test content</html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )
            html = await fetch_arxiv_html(
                "https://arxiv.org/html/2501.12345",
                arxiv_id="2501.12345",
                version=None,
                use_cache=False,
            )
            assert "Test content" in html
        cache_files = [p.name for p in tmp_path.rglob("*") if p.is_file()]
        assert cache_files == ["source.html"]
        assert not list(tmp_path.rglob("*.part"))


class TestExportMirror:
    """export.arxiv.org mirror fallback for arxiv.org 404 / rate-limit."""

    def _mock_cache(self, monkeypatch, tmp_path):
        from arxiv2md_beta.network import fetch as fetch_module

        monkeypatch.setattr(
            fetch_module,
            "_cache_dir_for",
            lambda arxiv_id, version: tmp_path / f"{arxiv_id}__{version or 'latest'}",
        )

    @pytest.mark.asyncio
    async def test_html_404_mirror_success(self, tmp_path, monkeypatch):
        """arxiv.org 404 → export mirror serves the HTML."""
        self._mock_cache(monkeypatch, tmp_path)
        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            route_mirror = respx.get("https://export.arxiv.org/html/2501.12345").mock(
                return_value=Response(
                    200,
                    text="<html>Mirror content</html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )
            html = await fetch_arxiv_html(
                "https://arxiv.org/html/2501.12345",
                arxiv_id="2501.12345",
                version=None,
                use_cache=False,
            )
            assert "Mirror content" in html
            assert route_mirror.call_count == 1

    @pytest.mark.asyncio
    async def test_mirror_disabled_no_mirror_request(self, tmp_path, monkeypatch):
        """urls.arxiv_mirror_host = '' disables mirror retries entirely."""
        from types import SimpleNamespace

        from arxiv2md_beta.network import fetch as fetch_module
        from arxiv2md_beta.network import mirror as mirror_module

        self._mock_cache(monkeypatch, tmp_path)
        fake = SimpleNamespace(
            urls=SimpleNamespace(arxiv_host="arxiv.org", arxiv_mirror_host=""),
            http=SimpleNamespace(mirror_on_404=False, mirror_on_rate_limit=False),
        )
        monkeypatch.setattr(mirror_module, "get_settings", lambda: fake)
        monkeypatch.setattr(fetch_module, "to_export_mirror", mirror_module.to_export_mirror)
        monkeypatch.setattr(fetch_module, "mirror_worth_try", mirror_module.mirror_worth_try)
        with respx.mock:
            route = respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(404, text="Not found", headers={"content-type": "text/html; charset=utf-8"})
            )
            route_mirror = respx.get("https://export.arxiv.org/html/2501.12345")
            with pytest.raises(NetworkError):
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,
                )
            assert route.call_count == 1
            assert not route_mirror.called

    def test_to_export_mirror_non_arxiv_host(self):
        from arxiv2md_beta.network.mirror import to_export_mirror

        assert to_export_mirror("https://ar5iv.labs.arxiv.org/html/2501.12345") is None

    def test_mirror_worth_try_non_network_error(self):
        from arxiv2md_beta.network.mirror import mirror_worth_try

        assert not mirror_worth_try(ValueError("nope"))


class TestPoisonedCacheSelfHeal:
    """A cached placeholder page must not fail every run until TTL expiry.

    The guard now drops the poisoned entry and re-downloads.
    """

    def test_placeholder_cache_is_discarded_and_refetched(self, tmp_path, monkeypatch):
        from arxiv2md_beta.network import fetch as fetch_module

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        html_path = cache_dir / "source.html"
        html_path.write_text("<html><head><title> No content available </title></head></html>", encoding="utf-8")
        monkeypatch.setattr(fetch_module, "_cache_dir_for", lambda arxiv_id, version: cache_dir)

        good_html = "<html><head><title>Real Paper</title></head><body>ok</body></html>"

        async def fake_fetch(url: str) -> str:
            return good_html

        monkeypatch.setattr(fetch_module, "_fetch_with_retries", fake_fetch)

        async def _run():
            return await fetch_module.fetch_arxiv_html(
                "https://arxiv.org/html/2501.11120",
                arxiv_id="2501.11120",
                version=None,
                use_cache=True,
            )

        import asyncio

        result = asyncio.run(_run())
        assert result == good_html
        # The poisoned entry was replaced with the fresh download.
        assert html_path.read_text(encoding="utf-8") == good_html
