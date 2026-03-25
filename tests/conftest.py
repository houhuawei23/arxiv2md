"""Pytest configuration and fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from arxiv2md_beta.settings import load_settings, reset_settings_cache


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
