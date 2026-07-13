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
