"""Tests for the ``search`` command building blocks."""

from __future__ import annotations

import pytest

from arxiv2md_beta.exceptions import UserInputError
from arxiv2md_beta.network.arxiv_api import parse_api_entries
from arxiv2md_beta.network.arxiv_search import build_search_query, build_search_url


def test_plain_phrase_is_quoted_in_field() -> None:
    assert build_search_query("fourier neural operator") == 'all:"fourier neural operator"'
    assert build_search_query("fourier neural operator", field="ti") == 'ti:"fourier neural operator"'


def test_field_expression_passthrough() -> None:
    q = 'ti:"attention" AND cat:cs.LG'
    assert build_search_query(q) == q


def test_author_filter_appends_and() -> None:
    got = build_search_query("some phrase", authors=["Smith", " Wang "])
    assert got == 'all:"some phrase" AND au:"Smith" AND au:"Wang"'


def test_invalid_field_raises() -> None:
    with pytest.raises(UserInputError):
        build_search_query("x", field="bogus")


def test_empty_query_raises() -> None:
    with pytest.raises(UserInputError):
        build_search_query("   ")


def test_build_search_url_encodes_and_formats() -> None:
    url = build_search_url("fourier operator", field="ti", sort="submitted", start=5, max_results=3)
    assert url.startswith("https://export.arxiv.org/api/query?")
    assert "start=5" in url
    assert "max_results=3" in url
    assert "sortBy=submittedDate" in url
    assert "ti%22" in url or "ti%3A" in url or "ti:" in url


def test_build_search_url_sort_relevance_maps_verbatim() -> None:
    # audit5 G4-3: the CLI's "submitted" is not a legal arXiv sortBy value —
    # the API silently fell back to relevance. External syntax is kept,
    # mapped to the API value inside the URL builder.
    url = build_search_url("fourier operator", sort="relevance")
    assert "sortBy=relevance" in url


def test_build_search_url_invalid_sort() -> None:
    with pytest.raises(UserInputError):
        build_search_url("x", sort="nope")


_SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2310.06825v1</id>
    <title>Mistral 7B</title>
    <summary>An abstract.</summary>
    <published>2023-10-10T17:59:31Z</published>
    <author><name>Albert Q. Jiang</name></author>
    <category term="cs.CL"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL"/>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/2310.06825v1"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2501.11120v2</id>
    <title>Another Paper</title>
    <summary>More text.</summary>
    <published>2025-01-20T00:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <category term="cs.LG"/>
  </entry>
</feed>
"""


def test_parse_api_entries_multiple() -> None:
    entries = parse_api_entries(_SAMPLE_FEED)
    assert len(entries) == 2
    assert entries[0]["arxiv_id"] == "2310.06825v1"
    assert entries[0]["title"] == "Mistral 7B"
    assert entries[0]["submission_date"] == "20231010"
    assert entries[0]["authors"] == [{"name": "Albert Q. Jiang"}]
    assert entries[1]["arxiv_id"] == "2501.11120v2"


def test_parse_api_entries_empty_feed() -> None:
    assert parse_api_entries("<feed xmlns='http://www.w3.org/2005/Atom'></feed>") == []
    assert parse_api_entries("not xml at all") == []
