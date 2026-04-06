"""Integration tests with HTTP mocking for arxiv2md-beta.

These tests use respx to mock HTTP responses, allowing fast and reliable
testing without network dependencies.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from arxiv2md_beta.network.fetch import fetch_arxiv_html


class TestHtmlFetching:
    """Tests for HTML fetching with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_fetch_arxiv_html_success(self, sample_html: str):
        """Test successful HTML fetch from arXiv."""
        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(
                    200,
                    text=sample_html,
                    headers={"content-type": "text/html; charset=utf-8"}
                )
            )

            html = await fetch_arxiv_html(
                "https://arxiv.org/html/2501.12345",
                arxiv_id="2501.12345",
                version=None,
                use_cache=False,
            )
            assert "A Sample Paper for Testing" in html
            assert html == sample_html

    @pytest.mark.asyncio
    async def test_fetch_arxiv_html_not_found(self):
        """Test HTML fetch with 404 response."""
        with respx.mock:
            respx.get("https://arxiv.org/html/2501.99999").mock(
                return_value=Response(
                    404,
                    text="Not found",
                    headers={"content-type": "text/html; charset=utf-8"}
                )
            )

            from arxiv2md_beta.exceptions import NetworkError
            with pytest.raises(NetworkError) as exc_info:
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.99999",
                    arxiv_id="2501.99999",
                    version=None,
                    use_cache=False,
                )
            assert "does not have an HTML version" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_arxiv_html_with_ar5iv_fallback(self, sample_html: str):
        """Test ar5iv fallback when arXiv returns 404."""
        with respx.mock:
            # Make arXiv return 404 but with specific message
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(
                    404,
                    text="does not have an HTML version",
                    headers={"content-type": "text/html; charset=utf-8"}
                )
            )
            # Make ar5iv return success
            respx.get("https://ar5iv.org/html/2501.12345").mock(
                return_value=Response(
                    200,
                    text=sample_html,
                    headers={"content-type": "text/html; charset=utf-8"}
                )
            )

            html = await fetch_arxiv_html(
                "https://arxiv.org/html/2501.12345",
                arxiv_id="2501.12345",
                version=None,
                ar5iv_url="https://ar5iv.org/html/2501.12345",
                use_cache=False,
            )
            assert "A Sample Paper for Testing" in html

    @pytest.mark.asyncio
    async def test_fetch_network_timeout(self):
        """Test handling of network timeout."""
        from httpx import ConnectTimeout

        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").side_effect = ConnectTimeout(
                "Connection timed out"
            )

            from arxiv2md_beta.exceptions import NetworkError
            with pytest.raises(NetworkError):
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,
                )

    @pytest.mark.asyncio
    async def test_invalid_html_response(self):
        """Test handling of non-HTML response."""
        with respx.mock:
            respx.get("https://arxiv.org/html/2501.12345").mock(
                return_value=Response(
                    200,
                    text='{"error": "not html"}',
                    headers={"content-type": "application/json"}
                )
            )

            from arxiv2md_beta.exceptions import NetworkError
            with pytest.raises((NetworkError, ValueError)):
                await fetch_arxiv_html(
                    "https://arxiv.org/html/2501.12345",
                    arxiv_id="2501.12345",
                    version=None,
                    use_cache=False,
                )
