"""Tests for arXiv id-derived submission dates and metadata defaults."""

from __future__ import annotations

from arxiv2md_beta.network.arxiv_api import (
    fill_arxiv_metadata_defaults,
    submission_date_from_new_style_arxiv_id,
)


def test_submission_date_from_new_style_id() -> None:
    assert submission_date_from_new_style_arxiv_id("2311.15127") == "20231101"
    assert submission_date_from_new_style_arxiv_id("2311.15127v2") == "20231101"
    assert submission_date_from_new_style_arxiv_id("1706.03762") == "20170601"


def test_submission_date_invalid_or_old_format() -> None:
    assert submission_date_from_new_style_arxiv_id("math/9901123") is None
    assert submission_date_from_new_style_arxiv_id("") is None


def test_fill_arxiv_metadata_defaults_sets_id_and_date() -> None:
    out = fill_arxiv_metadata_defaults({"title": None, "submission_date": None}, "2311.15127")
    assert out["arxiv_id"] == "2311.15127"
    assert out["submission_date"] == "20231101"
    assert out["date"] == "2023-11-01"
    assert out["year"] == "2023"


def test_fill_preserves_existing_submission_date() -> None:
    out = fill_arxiv_metadata_defaults(
        {"arxiv_id": "2311.15127", "submission_date": "20231115"},
        "2311.15127",
    )
    assert out["submission_date"] == "20231115"
