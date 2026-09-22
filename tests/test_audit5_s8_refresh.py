"""``paper-yml --refresh`` (audit5 S8 F3).

``--update`` protects user-owned fields (workflow/relations/bibtex) from API
overwrites, but there was no way to say "reset those from the API too" —
users had to hand-edit the YAML back. ``--refresh`` inverts the rule for
USER_OWNED_PATHS only: fresh API values win where the API provides one,
keys only the user added are still kept, and degraded-API protection
unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.cli.params import PaperYmlParams
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
    paper["urls"] = {"website": "https://example.com"}
    return existing


class TestMergeRefresh:
    def test_refresh_overwrites_user_owned_fields_with_fresh(self) -> None:
        merged = merge_paper_yml_preserve_user_fields(_with_user_edits(_fresh()), _fresh(), refresh=True)
        paper = merged["paper"]
        # The API's values (defaults) win on user-owned paths...
        assert paper["workflow"]["status"] != "read" or paper["workflow"] == _fresh()["paper"]["workflow"]
        assert paper["workflow"] == _fresh()["paper"]["workflow"]
        assert paper["relations"]["tags"] == _fresh()["paper"]["relations"]["tags"]

    def test_refresh_still_keeps_user_only_keys(self) -> None:
        merged = merge_paper_yml_preserve_user_fields(_with_user_edits(_fresh()), _fresh(), refresh=True)
        assert merged["paper"]["urls"] == {"website": "https://example.com"}

    def test_refresh_keeps_old_value_when_fresh_lacks_one(self) -> None:
        existing = _with_user_edits(_fresh())
        fresh = _fresh()
        del fresh["paper"]["relations"]["tags"]
        merged = merge_paper_yml_preserve_user_fields(existing, fresh, refresh=True)
        # Refresh must not destroy data the API did not replace.
        assert merged["paper"]["relations"]["tags"] == ["ml", "benchmark"]

    def test_default_unchanged(self) -> None:
        merged = merge_paper_yml_preserve_user_fields(_with_user_edits(_fresh()), _fresh())
        assert merged["paper"]["workflow"]["status"] == "read"


class TestRunnerRefresh:
    async def test_update_flow_applies_refresh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yml_path = tmp_path / "paper.yml"
        write_paper_yml_file({"arxiv_id": "2501.00000", "title": "Old"}, yml_path)
        write_paper_yml_file(
            {"arxiv_id": "2501.00000", "title": "Old"},
            yml_path,
            merge_existing=_with_user_edits(load_paper_yml(yml_path)),
        )
        assert load_paper_yml(yml_path)["paper"]["workflow"]["status"] == "read"

        async def _ok(aid: str, **kwargs: object) -> dict:
            return {"arxiv_id": aid, "title": "Fresh Title", "authors": [{"name": "A. Author"}]}

        async def _no_tex(meta: dict, *args: object, **kwargs: object) -> None:
            return None

        monkeypatch.setattr("arxiv2md_beta.cli.runner.paper_yml.fetch_arxiv_metadata", _ok)
        monkeypatch.setattr("arxiv2md_beta.cli.runner.paper_yml.fetch_and_merge_tex_affiliations_for_metadata", _no_tex)
        params = PaperYmlParams(update_path=yml_path, arxiv_input=None, output=None, force=False, refresh=True)
        await run_paper_yml_flow(params)
        merged = load_paper_yml(yml_path)
        assert merged["paper"]["title"] == "Fresh Title"
        assert merged["paper"]["workflow"]["status"] != "read"
        # User-only keys survive the refresh.
        assert merged["paper"]["urls"] == {"website": "https://example.com"}


class TestCliRefresh:
    def test_flag_propagates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from typer.testing import CliRunner

        from arxiv2md_beta.cli.app import app

        yml = tmp_path / "paper.yml"
        write_paper_yml_file(_metadata_to_paper_yml({"arxiv_id": "2501.00000", "title": "T"}), yml)
        captured: list[PaperYmlParams] = []

        def fake_runner(params: PaperYmlParams) -> Path:
            captured.append(params)
            return yml

        monkeypatch.setattr("arxiv2md_beta.cli.app.run_paper_yml_sync", fake_runner)
        r = CliRunner().invoke(app, ["paper-yml", "--update", str(yml), "--refresh"])
        assert r.exit_code == 0, r.output
        assert captured and captured[0].refresh is True

    def test_default_is_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from typer.testing import CliRunner

        from arxiv2md_beta.cli.app import app

        yml = tmp_path / "paper.yml"
        write_paper_yml_file(_metadata_to_paper_yml({"arxiv_id": "2501.00000", "title": "T"}), yml)
        captured: list[PaperYmlParams] = []

        def fake_runner(params: PaperYmlParams) -> Path:
            captured.append(params)
            return yml

        monkeypatch.setattr("arxiv2md_beta.cli.app.run_paper_yml_sync", fake_runner)
        r = CliRunner().invoke(app, ["paper-yml", "--update", str(yml)])
        assert r.exit_code == 0, r.output
        assert captured and captured[0].refresh is False
