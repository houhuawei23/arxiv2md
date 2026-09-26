"""--concurrency for single-paper ``convert`` (audit5 S8 F2).

Batch parallelism has ``-j``, but a single conversion's per-paper
parallelism was only reachable by editing a config file. The flag now
travels on ``ConvertParams.concurrency`` (Phase 3 parameter unification:
no process-global settings mutation) and defaults to None = the configured
``images.max_concurrency`` applies.
"""

from __future__ import annotations

from typer.testing import CliRunner

from arxiv2md_beta.cli.app import app
from arxiv2md_beta.settings import reset_settings_cache


def _invoke_convert(extra_args: list[str], monkeypatch) -> list:  # type: ignore[no-untyped-def]
    reset_settings_cache()
    captured: list = []

    def fake_runner(params) -> None:  # type: ignore[no-untyped-def]
        captured.append(params)

    monkeypatch.setattr("arxiv2md_beta.cli.app.run_convert_sync", fake_runner)
    r = CliRunner().invoke(app, ["convert", "2401.12345", *extra_args])
    assert r.exit_code == 0, r.output
    return captured


class TestConvertConcurrency:
    def test_flag_reaches_params(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        captured = _invoke_convert(["--concurrency", "8"], monkeypatch)
        assert captured[0].concurrency == 8

    def test_short_flag_works(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        captured = _invoke_convert(["-c", "2"], monkeypatch)
        assert captured[0].concurrency == 2

    def test_default_leaves_configured_value(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        captured = _invoke_convert([], monkeypatch)
        assert captured[0].concurrency is None
        from arxiv2md_beta.settings import get_settings

        assert get_settings().images.max_concurrency == 4  # bundled default

    def test_zero_rejected_by_validation(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        reset_settings_cache()
        r = CliRunner().invoke(app, ["convert", "2401.12345", "--concurrency", "0"])
        assert r.exit_code != 0
