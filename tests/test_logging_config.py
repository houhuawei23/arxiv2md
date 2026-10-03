"""Per-run file logging: label resolution, log file layout, retention cleanup."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from arxiv2md_beta.settings import load_settings, reset_settings_cache
from arxiv2md_beta.utils.logging_config import (
    configure_logging,
    get_log_file,
    log_run_result,
    reset_run_state,
    resolve_run_label,
    sanitize_label,
)


@pytest.fixture(autouse=True)
def _reset_run_state() -> None:  # noqa: PT004 - fixture mutates global state by design
    reset_run_state()
    yield
    reset_run_state()


# ── Label resolution ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "subcommand", "expected"),
    [
        (["convert", "2501.11120", "-o", "out"], "convert", "2501.11120"),
        (["convert", "-o", "outputs", "2501.11120"], "convert", "2501.11120"),
        (["convert", "--output", "out", "2501.11120v2"], "convert", "2501.11120v2"),
        (["batch", "inputs.txt", "-o", "out"], "batch", "inputs.txt"),
        (["convert", "https://arxiv.org/abs/2609.38850"], "convert", "2609.38850"),
        (["convert", "--verbose"], "convert", "run"),
        ([], "convert", "run"),
        (["convert", "2501.11120"], None, "cli"),
    ],
)
def test_resolve_run_label(argv: list[str], subcommand: str | None, expected: str) -> None:
    assert resolve_run_label(argv, subcommand) == expected


def test_sanitize_label_replaces_unsafe_characters() -> None:
    assert sanitize_label("a b c:d") == "a-b-c-d"
    assert sanitize_label("some/local/path.txt") == "path.txt"  # basenames win
    assert sanitize_label("") == "run"
    long = "x" * 100
    assert len(sanitize_label(long)) == 60


# ── Per-run log file ─────────────────────────────────────────────────────────


def _settings_with_logs_dir(tmp_path: Path):  # type: ignore[no-untyped-def]
    reset_settings_cache()
    s = load_settings(environment="test", force_reload=True)
    return s.model_copy(
        update={
            "logging": s.logging.model_copy(update={"dir": str(tmp_path / "logs")}),
            "cache": s.cache.model_copy(update={"dir": str(tmp_path / "cache")}),
        }
    )


def test_per_run_log_file_layout_and_content(tmp_path: Path) -> None:
    s = _settings_with_logs_dir(tmp_path)
    # level="INFO" like a normal CLI run (the test profile defaults to WARNING)
    log_path = configure_logging(settings=s, level="INFO", subcommand="convert")
    assert log_path is not None
    day_dir = log_path.parent
    assert day_dir.parent == (tmp_path / "logs")
    assert day_dir.name == datetime.now().astimezone().strftime("%Y-%m-%d")
    # <stamp>-<subcommand>-<label>.log; under pytest argv carries no
    # positional, so the label falls back to "run"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-convert-run", log_path.stem)
    assert get_log_file() == log_path

    from loguru import logger

    logger.info("hello run")
    log_run_result(status="success", exit_code=0)

    text = log_path.read_text(encoding="utf-8")
    assert "Run started | command:" in text
    assert "Run context | version=" in text
    assert "| +00:00:00." in text  # elapsed column present
    assert "hello run" in text
    assert "Run finished | result=success exit_code=0 elapsed=" in text


def test_configure_logging_returns_none_when_disabled(tmp_path: Path) -> None:
    s = _settings_with_logs_dir(tmp_path)
    s = s.model_copy(update={"features": s.features.model_copy(update={"enable_file_logging": False})})
    assert configure_logging(settings=s, subcommand="convert") is None
    assert get_log_file() is None


# ── Retention cleanup ────────────────────────────────────────────────────────


def test_retention_cleanup_deletes_old_day_dirs(tmp_path: Path) -> None:
    from arxiv2md_beta.utils.logging_config import _cleanup_old_day_dirs

    logs_dir = tmp_path / "logs"
    today = datetime.now()
    for offset in (40, 1):
        day_dir = logs_dir / (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True)
        (day_dir / "run.log").write_text("x", encoding="utf-8")
    (logs_dir / "not-a-day-dir").mkdir()

    _cleanup_old_day_dirs(logs_dir, retention_days=30, now=today)

    assert not (logs_dir / (today - timedelta(days=40)).strftime("%Y-%m-%d")).exists()
    assert (logs_dir / (today - timedelta(days=1)).strftime("%Y-%m-%d")).exists()
    assert (logs_dir / "not-a-day-dir").exists()


def test_retention_disabled_keeps_everything(tmp_path: Path) -> None:
    from arxiv2md_beta.utils.logging_config import _cleanup_old_day_dirs

    logs_dir = tmp_path / "logs"
    ancient = logs_dir / "2020-01-01"
    ancient.mkdir(parents=True)
    _cleanup_old_day_dirs(logs_dir, retention_days=0, now=datetime.now())
    assert ancient.exists()
