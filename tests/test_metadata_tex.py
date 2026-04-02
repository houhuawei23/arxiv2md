"""Tests for shared TeX affiliation helpers used by convert and paper-yml."""

from __future__ import annotations

from arxiv2md_beta.output.metadata_tex import merge_tex_affiliations_if_configured


def test_merge_tex_if_configured_no_tex_returns_zero() -> None:
    assert merge_tex_affiliations_if_configured({"authors": [{"name": "A"}]}, None) == 0
