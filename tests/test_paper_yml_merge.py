"""Tests for paper.yml user-field protection and the --update guard (audit4 A4)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from arxiv2md_beta.cli.runner.paper_yml import run_paper_yml_flow
from arxiv2md_beta.output.metadata import (
    _metadata_to_paper_yml,
    load_paper_yml,
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
    assert fresh["paper"]["workflow"]["date_added"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


class TestConvertPathPreservesUserFields:
    """audit5 C6: the convert path must merge like --update does.

    save_paper_metadata (called by ir_finalize/orchestrator on every convert)
    wrote the fresh dict wholesale, so re-running convert in an existing
    output directory reset hand-edited workflow/relations/bibtex fields even
    though the audit4 A4 protection covered `paper-yml --update`.
    """

    def test_reconvert_preserves_user_edits(self, tmp_path: Path) -> None:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        target = tmp_path / "paper.yml"
        save_paper_metadata(
            {
                "arxiv_id": "2501.00000",
                "title": "Fresh Title",
                "authors": [{"name": "A. Author"}],
            },
            tmp_path,
        )
        # Simulate the user hand-editing fields after the first run.
        existing = load_paper_yml(target)
        paper = existing["paper"]
        paper["workflow"]["status"] = "read"
        paper["workflow"]["priority"] = "high"
        paper["workflow"]["date_added"] = "2020-05-05"
        paper["relations"]["tags"] = ["ml"]
        paper["bibtex"] = "@article{hand, title={Hand}}"
        import yaml as _yaml

        target.write_text(_yaml.dump(existing, allow_unicode=True), encoding="utf-8")

        # Re-run convert with slightly different API metadata.
        save_paper_metadata(
            {
                "arxiv_id": "2501.00000",
                "title": "Refined Title",
                "authors": [{"name": "A. Author"}, {"name": "B. Coauthor"}],
            },
            tmp_path,
        )
        merged = load_paper_yml(target)["paper"]
        assert merged["title"] == "Refined Title"  # fresh API values win
        assert len(merged["authors"]) == 2
        assert merged["workflow"]["status"] == "read"  # user edits survive
        assert merged["workflow"]["priority"] == "high"
        assert merged["workflow"]["date_added"] == "2020-05-05"
        assert merged["relations"]["tags"] == ["ml"]
        assert merged["bibtex"] == "@article{hand, title={Hand}}"

    def test_unreadable_existing_file_degrades_to_overwrite(self, tmp_path: Path) -> None:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        target = tmp_path / "paper.yml"
        target.write_text("{ not: valid: yaml: [", encoding="utf-8")
        save_paper_metadata(
            {"arxiv_id": "2501.00000", "title": "Fresh"},
            tmp_path,
        )
        paper = load_paper_yml(target)["paper"]
        assert paper["title"] == "Fresh"

    def test_fresh_directory_writes_normally(self, tmp_path: Path) -> None:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        save_paper_metadata({"arxiv_id": "2501.00000", "title": "First Run"}, tmp_path)
        paper = load_paper_yml(tmp_path / "paper.yml")["paper"]
        assert paper["title"] == "First Run"
        assert paper["workflow"]["status"] == "unread"
