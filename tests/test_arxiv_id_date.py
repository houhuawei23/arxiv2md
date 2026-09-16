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


def test_resolve_submission_date_prefers_api() -> None:
    from arxiv2md_beta.network.arxiv_api import resolve_submission_date

    assert resolve_submission_date(api_date="20231115", html_date="20260901", arxiv_id="2311.15127") == "20231115"


def test_resolve_submission_date_mismatch_trusts_id() -> None:
    """HTML date from a later version loses against the id's YYMM (v1 month)."""
    from arxiv2md_beta.network.arxiv_api import resolve_submission_date

    assert resolve_submission_date(api_date=None, html_date="20260901", arxiv_id="2311.15127") == "20231101"


def test_resolve_submission_date_agreement_keeps_html() -> None:
    from arxiv2md_beta.network.arxiv_api import resolve_submission_date

    assert resolve_submission_date(api_date=None, html_date="20231120", arxiv_id="2311.15127") == "20231120"


def test_resolve_submission_date_old_style_id_falls_back_to_html() -> None:
    from arxiv2md_beta.network.arxiv_api import resolve_submission_date

    assert resolve_submission_date(api_date=None, html_date="19990101", arxiv_id="math/9901123") == "19990101"
    assert resolve_submission_date(api_date=None, html_date=None, arxiv_id="math/9901123") is None
