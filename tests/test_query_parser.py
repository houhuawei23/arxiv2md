"""Tests for query parser."""

from __future__ import annotations

import pytest

from arxiv2md_beta.query_parser import parse_arxiv_input


def test_parse_arxiv_id():
    """Test parsing arXiv ID."""
    query = parse_arxiv_input("2501.11120")
    assert query.arxiv_id == "2501.11120"
    assert query.version is None
    assert "2501.11120" in query.html_url


def test_parse_arxiv_id_with_version():
    """Test parsing arXiv ID with version."""
    query = parse_arxiv_input("2501.11120v1")
    assert query.arxiv_id == "2501.11120v1"
    assert query.version == "v1"


def test_parse_arxiv_url():
    """Test parsing arXiv URL."""
    query = parse_arxiv_input("https://arxiv.org/abs/2501.11120")
    assert query.arxiv_id == "2501.11120"


def test_parse_arxiv_html_url():
    """Test parsing arXiv HTML URL."""
    query = parse_arxiv_input("https://arxiv.org/html/2501.11120")
    assert query.arxiv_id == "2501.11120"


def test_parse_invalid_input():
    """Test parsing invalid input."""
    with pytest.raises(ValueError):
        parse_arxiv_input("invalid")
