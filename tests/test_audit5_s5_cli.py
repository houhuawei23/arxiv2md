"""audit5 S5 CLI regression tests (G4-1, G4-6, T-9)."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from arxiv2md_beta.cli.app import app
from arxiv2md_beta.cli.params import PaperYmlParams
from arxiv2md_beta.settings import reset_settings_cache


def _write_minimal_paper_yml(path: Path) -> None:
    path.write_text(
        yaml.dump(
            {
                "paper": {
                    "id": "2401.00001",
                    "title": "Some Title",
                    "identifiers": {"arxiv": "2401.00001"},
                }
            }
        ),
        encoding="utf-8",
    )


class TestUpdateForcePropagation:
    """audit5 G4-1: ``--update --force`` must reach the runner.

    The CLI hardcoded ``force=False`` in the --update branch, so the error
    message telling users to "pass --force" led to the same refusal forever.
    """

    def test_update_branch_propagates_force(self, tmp_path: Path, monkeypatch) -> None:
        reset_settings_cache()
        yml = tmp_path / "paper.yml"
        _write_minimal_paper_yml(yml)
        captured: list[PaperYmlParams] = []

        def fake_runner(params: PaperYmlParams) -> Path:
            captured.append(params)
            return yml

        monkeypatch.setattr("arxiv2md_beta.cli.app.run_paper_yml_sync", fake_runner)
        runner = CliRunner()
        r = runner.invoke(app, ["paper-yml", "--update", str(yml), "--force"])
        assert r.exit_code == 0, r.output
        assert captured and captured[0].force is True
        assert captured[0].update_path is not None

    def test_update_without_force_stays_false(self, tmp_path: Path, monkeypatch) -> None:
        reset_settings_cache()
        yml = tmp_path / "paper.yml"
        _write_minimal_paper_yml(yml)
        captured: list[PaperYmlParams] = []

        def fake_runner(params: PaperYmlParams) -> Path:
            captured.append(params)
            return yml

        monkeypatch.setattr("arxiv2md_beta.cli.app.run_paper_yml_sync", fake_runner)
        runner = CliRunner()
        r = runner.invoke(app, ["paper-yml", "--update", str(yml)])
        assert r.exit_code == 0, r.output
        assert captured and captured[0].force is False
