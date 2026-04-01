"""Tests for abs HTML author parsing, OpenAlex merge, and paper.yml author fields."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from arxiv2md_beta.network.arxiv_abs_html import parse_abs_page_for_authors
from arxiv2md_beta.network.author_enrichment import _dedupe_affiliation_strings, _merge_openalex_into_authors
from arxiv2md_beta.output.metadata import _metadata_to_paper_yml


def test_dedupe_affiliation_strings_drops_substrings() -> None:
    got = _dedupe_affiliation_strings(
        [
            "Google (United States)",
            "Google Brain",
            "Google (United States), Mountain View, United States",
        ]
    )
    assert got == [
        "Google Brain",
        "Google (United States), Mountain View, United States",
    ]


def test_parse_abs_page_for_authors_names_and_meta_institution() -> None:
    html = """
    <html><body>
    <div class="authors">
      <a href="/search/...">Alice Example</a>
      <a href="/search/...">Bob Sample</a>
    </div>
    <div id="abs">
      <meta name="citation_author_institution" content="University of Test">
    </div>
    </body></html>
    """
    names, hints = parse_abs_page_for_authors(html)
    assert names == ["Alice Example", "Bob Sample"]
    assert "University of Test" in hints


def test_merge_openalex_into_authors_sets_affiliations_and_orcid() -> None:
    authors = [{"name": "Jane Doe"}]
    work = {
        "authorships": [
            {
                "author": {"display_name": "Jane Doe", "orcid": "https://orcid.org/0000-0002-1825-0097"},
                "institutions": [{"display_name": "OpenAlex Lab"}],
                "raw_affiliation_strings": ["Dept. of AI"],
            }
        ]
    }
    _merge_openalex_into_authors(authors, work)
    assert authors[0].get("affiliation")
    assert "OpenAlex Lab" in authors[0]["affiliation"]
    assert authors[0].get("affiliations") == ["OpenAlex Lab", "Dept. of AI"]
    assert authors[0].get("orcid") == "0000-0002-1825-0097"


@pytest.mark.asyncio
async def test_enrich_authors_openalex_sets_metadata_work_id() -> None:
    from arxiv2md_beta.network.author_enrichment import enrich_authors_with_abs_html_and_openalex

    meta = {
        "authors": [{"name": "Jane Doe"}],
    }
    work = {
        "id": "https://w.openalex.org/W123",
        "authorships": [
            {
                "author": {"display_name": "Jane Doe"},
                "institutions": [{"display_name": "Inst X"}],
            }
        ],
    }
    with (
        patch(
            "arxiv2md_beta.network.author_enrichment.fetch_abs_html",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "arxiv2md_beta.network.author_enrichment.fetch_openalex_work_for_arxiv",
            new_callable=AsyncMock,
            return_value=work,
        ),
    ):
        out = await enrich_authors_with_abs_html_and_openalex(meta, "2301.00001")
    assert out["openalex_work_id"] == "https://w.openalex.org/W123"
    assert out["authors"][0].get("affiliation")


def test_metadata_to_paper_yml_includes_affiliations_and_openalex() -> None:
    md = {
        "arxiv_id": "1706.03762",
        "title": "Attention",
        "year": 2017,
        "published": "2017-06-12T00:00:00Z",
        "authors": [
            {
                "name": "A",
                "affiliation": "Google",
                "affiliations": ["Google", "Brain"],
                "orcid": "0000-0001-2345-6789",
            }
        ],
        "openalex_work_id": "https://openalex.org/W123",
    }
    yml = _metadata_to_paper_yml(md)
    assert yml["paper"]["identifiers"]["openalex_work"] == "https://openalex.org/W123"
    au0 = yml["paper"]["authors"][0]
    assert au0["affiliations"] == ["Google", "Brain"]
    assert "affiliation" not in au0
    assert au0["orcid"] == "0000-0001-2345-6789"


def test_metadata_to_paper_yml_affiliation_string_when_no_list() -> None:
    md = {
        "arxiv_id": "1234.5678",
        "title": "T",
        "year": 2020,
        "published": "2020-01-01T00:00:00Z",
        "authors": [{"name": "Only Str", "affiliation": "MIT"}],
    }
    yml = _metadata_to_paper_yml(md)
    assert yml["paper"]["authors"][0] == {"name": "Only Str", "affiliation": "MIT"}
