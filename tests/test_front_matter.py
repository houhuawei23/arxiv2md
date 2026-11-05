"""Flat YAML front matter for paper.md and its assembly in format_output."""

from __future__ import annotations

import yaml

from arxiv2md_beta.ingestion.persist import format_output
from arxiv2md_beta.output.front_matter import build_front_matter
from arxiv2md_beta.output.metadata import _metadata_to_paper_yml

PAPER = _metadata_to_paper_yml(
    {
        "arxiv_id": "2609.36054v2",
        "title": "What if automating AI R&D triggers an intelligence explosion?",
        "published": "2026-09-28T00:00:00Z",
        "year": 2026,
        "categories": ["cs.CY", "cs.AI"],
        "pdf_url": "https://arxiv.org/pdf/2609.36054v2",
        "abstract_url": "https://arxiv.org/abs/2609.36054v2",
        "authors": [{"name": "Alan Chan", "affiliations": ["GovAI"], "orcid": "0000-0001-7547-3951"}],
    }
)["paper"]


def test_front_matter_is_flat_and_matches_reference_layout() -> None:
    fm_text = build_front_matter(PAPER, sections=18, today="2026-11-05")
    assert fm_text.startswith("---\n") and fm_text.endswith("---")
    fm = yaml.safe_load(fm_text[3:].rstrip("-\n "))
    assert list(fm.keys()) == [
        "title",
        "arxiv",
        "doi",
        "authors",
        "publication",
        "meta_status",
        "meta_checked",
        "workflow",
        "urls",
        "categories",
        "sections",
    ]
    assert fm["doi"] == "10.48550/arXiv.2609.36054"
    assert fm["arxiv"] == "2609.36054v2"
    assert fm["authors"][0] == {"name": "Alan Chan", "affiliations": ["GovAI"], "orcid": "0000-0001-7547-3951"}
    assert fm["publication"]["date_published"] == "2026-09-28"
    assert fm["meta_status"] == "pending" and fm["meta_checked"] == "2026-11-05"
    assert fm["workflow"]["date_added"] == PAPER["workflow"]["date_added"]
    assert fm["categories"] == ["cs.CY", "cs.AI"]
    assert fm["sections"] == 18
    # parser-facing text body stays out of the header block
    assert "abstract" not in fm and "bibtex" not in fm


def test_front_matter_drops_empty_optional_sections() -> None:
    minimal = {"paper": {"title": "T", "identifiers": {"arxiv": "1234.5678"}}}
    fm_text = build_front_matter(minimal["paper"], sections=3, today="2026-11-05")
    fm = yaml.safe_load(fm_text[3:].rstrip("-\n "))
    assert "authors" not in fm and "doi" not in fm and "urls" not in fm and "categories" not in fm
    assert fm["title"] == "T" and fm["sections"] == 3


def test_front_matter_empty_without_identity() -> None:
    assert build_front_matter({}, sections=1) == ""


def test_format_output_prepends_front_matter_and_h1() -> None:
    fm_text = build_front_matter(PAPER, sections=2, today="2026-11-05")
    out = format_output(
        "# Title: T\n- ArXiv: x",
        "tree",
        "## Abstract\n\nbody",
        include_tree=False,
        front_matter=fm_text,
        title=PAPER["title"],
    )
    assert out.startswith(fm_text + "\n\n# What if automating AI R&D")
    assert "## Abstract" in out
    assert "- ArXiv:" not in out  # bullet summary replaced in the file
    # summary keeps its stdout role when no front matter is given
    plain = format_output("S", "T", "C", include_tree=True)
    assert plain == "S\n\nT\n\nC"
