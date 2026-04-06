"""Pytest configuration and fixtures."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import respx
from httpx import Response

from arxiv2md_beta.settings import load_settings, reset_settings_cache
from tests.fixtures import FIXTURES_DIR


@pytest.fixture(autouse=True)
def _test_settings():
    """Use bundled environments/test.yml and a clean settings cache per test."""
    reset_settings_cache()
    load_settings(environment="test", force_reload=True)
    yield
    reset_settings_cache()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_html() -> str:
    """Load sample arXiv HTML content."""
    return (FIXTURES_DIR / "sample_arxiv.html").read_text(encoding="utf-8")


@pytest.fixture
def sample_metadata() -> dict:
    """Load sample arXiv metadata."""
    content = (FIXTURES_DIR / "sample_metadata.json").read_text(encoding="utf-8")
    return json.loads(content)


@pytest.fixture
def mock_arxiv_html(sample_html: str) -> respx.MockRouter:
    """Mock arXiv HTML endpoint.

    Note: This fixture should be used explicitly by tests that need it.
    It mocks only a specific URL pattern.
    """
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        # Mock specific test URL
        router.get(
            url__regex=r"https://arxiv\.org/html/2501\.12345$"
        ).mock(return_value=Response(
            200,
            text=sample_html,
            headers={"content-type": "text/html; charset=utf-8"}
        ))
        yield router


@pytest.fixture
def mock_arxiv_api(sample_metadata: dict) -> respx.MockRouter:
    """Mock arXiv API endpoint."""
    with respx.mock(assert_all_mocked=False) as router:
        # API query endpoint
        api_response = {
            "feed": {
                "entry": [{
                    "id": f"http://arxiv.org/abs/{sample_metadata['arxiv_id']}",
                    "title": sample_metadata["title"],
                    "author": [{"name": name} for name in sample_metadata["authors"]],
                    "summary": sample_metadata["abstract"],
                    "published": sample_metadata["published"],
                    "arxiv:primary_category": {"@term": sample_metadata["primary_category"]},
                    "category": [{"@term": cat} for cat in sample_metadata["categories"]],
                    "link": [
                        {"@rel": "alternate", "@href": sample_metadata["links"]["abs"]},
                        {"@rel": "related", "@type": "application/pdf", "@href": sample_metadata["links"]["pdf"]},
                    ],
                }]
            }
        }
        router.get(
            "http://export.arxiv.org/api/query"
        ).mock(return_value=Response(200, json=api_response))
        yield router


@pytest.fixture
def mock_ar5iv_fallback(sample_html: str) -> respx.MockRouter:
    """Mock ar5iv fallback endpoint."""
    with respx.mock(assert_all_mocked=False) as router:
        router.get(
            url__regex=r"https://ar5iv\.org/html/\d+\.\d+"
        ).mock(return_value=Response(
            200,
            text=sample_html,
            headers={"content-type": "text/html; charset=utf-8"}
        ))
        yield router
