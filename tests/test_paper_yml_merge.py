"""Tests for paper.yml user-field protection and the --update guard (audit4 A4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from arxiv2md_beta.cli.runner.paper_yml import run_paper_yml_flow
from arxiv2md_beta.output.metadata import (
    _metadata_to_paper_yml,
    merge_paper_yml_preserve_user_fields,
    write_paper_yml_file,
)


def _fresh() -> dict:
    return _metadata_to_paper_yml(
        {
            "arxiv_id": "2501.00000",
            "title": "Fresh Title",
            "authors": [{"name": "A. Author"}],
            "submission_date": "2025-01-01",
        }
    )


def _with_user_edits(existing: dict) -> dict:
    paper = existing["paper"]
    paper["workflow"] = {"status": "read", "priority": "high", "date_added": "2020-05-05"}
    paper["relations"]["tags"] = ["ml", "benchmark"]
    paper["relations"]["related"] = ["2401.99999"]
    return existing


def test_update_preserves_user_owned_fields() -> None:
    """Regression (audit4 A4): workflow/relations/bibtex reset on --update."""
    existing = _with_user_edits(_fresh())
    merged = merge_paper_yml_preserve_user_fields(existing, _fresh())
    paper = merged["paper"]
    assert paper["workflow"] == {"status": "read", "priority": "high", "date_added": "2020-05-05"}
    assert paper["relations"]["tags"] == ["ml", "benchmark"]
    assert paper["relations"]["related"] == ["2401.99999"]
    # Fresh fields still update.
    assert paper["title"] == "Fresh Title"


def test_missing_user_keys_are_filled_from_fresh() -> None:
    existing = _fresh()
    del existing["paper"]["workflow"]
    merged = merge_paper_yml_preserve_user_fields(existing, _fresh())
    assert merged["paper"]["workflow"]["status"] == "unread"


def test_date_added_is_ingestion_date_not_publication_date() -> None:
    """Regression (audit4 A4): date_added used to be the arXiv pub date."""
    fresh = _metadata_to_paper_yml({"arxiv_id": "2501.00000", "title": "T", "submission_date": "2020-01-01"})
    assert fresh["paper"]["workflow"]["date_added"] == datetime.now().strftime("%Y-%m-%d")


async def test_update_refuses_degraded_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """API degradation (title=None skeleton) must not overwrite the user file."""
    yml_path = tmp_path / "paper.yml"
    write_paper_yml_file(
        {"arxiv_id": "2501.00000", "title": "Real Title"},
        yml_path,
    )
    before = yml_path.read_text(encoding="utf-8")

    async def _degraded(aid: str, **kwargs: object) -> dict:
        return {"arxiv_id": aid, "title": None}

    monkeypatch.setattr("arxiv2md_beta.cli.runner.paper_yml.fetch_arxiv_metadata", _degraded)
    from arxiv2md_beta.cli.params import PaperYmlParams
    from arxiv2md_beta.exceptions import UserInputError

    params = PaperYmlParams(update_path=yml_path, arxiv_input=None, output=None, force=False)
    with pytest.raises(UserInputError, match="refusing to overwrite"):
        await run_paper_yml_flow(params)
    assert yml_path.read_text(encoding="utf-8") == before
