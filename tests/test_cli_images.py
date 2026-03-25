"""Typer CLI smoke tests (no network)."""

from __future__ import annotations

from typer.testing import CliRunner

from arxiv2md_beta.cli.app import app
from arxiv2md_beta.settings import reset_settings_cache


def test_images_help() -> None:
    reset_settings_cache()
    runner = CliRunner()
    r = runner.invoke(app, ["images", "--help"])
    assert r.exit_code == 0
    assert "images" in r.output.lower() or "arxiv" in r.output.lower()


def test_convert_help() -> None:
    reset_settings_cache()
    runner = CliRunner()
    r = runner.invoke(app, ["convert", "--help"])
    assert r.exit_code == 0


def test_convert_invalid_parser_exits_2() -> None:
    reset_settings_cache()
    runner = CliRunner()
    r = runner.invoke(app, ["convert", "2401.00001", "--parser", "bad"])
    assert r.exit_code == 2


def test_images_invocation_parses_options() -> None:
    """Dry-run style: --help for images with output option listed."""
    reset_settings_cache()
    runner = CliRunner()
    r = runner.invoke(app, ["images", "--help"])
    assert r.exit_code == 0
    assert "--output" in r.output or "-o" in r.output
