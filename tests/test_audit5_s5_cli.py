"""audit5 S5 CLI regression tests (G4-1, G4-6, T-9, R-10)."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from arxiv2md_beta.cli.app import app
from arxiv2md_beta.cli.params import PaperYmlParams
from arxiv2md_beta.settings import AppSettings, reset_settings_cache


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


class TestConfigInitMatchesSchema:
    """audit5 G4-6: ``config init`` output must equal the schema defaults.

    The hand-written starter drifted (dpi 200 vs 150, backoff 1.0 vs 3.0,
    missing whole sections) and hardcoded the version into user_agent, which
    defeated the settings-layer version injection. Serializing the validated
    default bundle makes drift impossible.
    """

    def test_init_output_matches_schema_defaults(self, tmp_path: Path) -> None:
        reset_settings_cache()
        out = tmp_path / "config.yml"
        runner = CliRunner()
        r = runner.invoke(app, ["config", "init", "--output", str(out), "--force"])
        assert r.exit_code == 0, r.output
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        # Valid against the schema and free of the historical drift values.
        AppSettings.model_validate(loaded)
        assert loaded["images"]["pdf_to_png_dpi"] == 150
        assert loaded["http"]["fetch_backoff_s"] == 3.0

    def test_user_agent_not_version_hardcoded(self, tmp_path: Path) -> None:
        reset_settings_cache()
        out = tmp_path / "config.yml"
        runner = CliRunner()
        r = runner.invoke(app, ["config", "init", "--output", str(out), "--force"])
        assert r.exit_code == 0, r.output
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        # The settings layer injects the package version at load time; a
        # literal "arxiv2md-beta/<version>" frozen into the file would never
        # update. The placeholder must survive into the starter.
        assert loaded["http"]["user_agent"] == "arxiv2md-beta"


def _bundle_defaults() -> dict:
    from arxiv2md_beta.settings.loader import _load_yaml_bytes, _read_resource

    return _load_yaml_bytes(_read_resource("arxiv2md_beta.config", "default_config.yml"))


class TestManifestStubStatus:
    """audit5 R-10: a settings-level allow_stub must be auditable too.

    ``ensure_not_stub`` bypasses on ``cli OR settings``, but the manifest
    only recorded ``allowed_stub`` for the CLI flag — a settings-level stub
    pass-through was written to disk and reported as ``ok``.
    """

    def _settings(self, allow_stub: bool) -> AppSettings:
        s = AppSettings.model_validate(_bundle_defaults())
        return s.model_copy(update={"output": s.output.model_copy(update={"allow_stub": allow_stub})})

    def test_settings_level_allow_stub_marks_allowed_stub(self) -> None:
        from arxiv2md_beta.cli.output_finalize import _manifest_stub_status

        status = _manifest_stub_status("too short", cli_allow_stub=False, settings=self._settings(True))
        assert status == "allowed_stub"

    def test_cli_flag_still_marks_allowed_stub(self) -> None:
        from arxiv2md_beta.cli.output_finalize import _manifest_stub_status

        status = _manifest_stub_status("too short", cli_allow_stub=True, settings=self._settings(False))
        assert status == "allowed_stub"

    def test_stub_disallowed_marks_ok(self) -> None:
        from arxiv2md_beta.cli.output_finalize import _manifest_stub_status

        status = _manifest_stub_status("too short", cli_allow_stub=False, settings=self._settings(False))
        assert status == "ok"

    def test_real_output_marks_ok_even_when_allowed(self) -> None:
        from arxiv2md_beta.cli.output_finalize import _manifest_stub_status

        status = _manifest_stub_status("x" * 20000, cli_allow_stub=True, settings=self._settings(True))
        assert status == "ok"
