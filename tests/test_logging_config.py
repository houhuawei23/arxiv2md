"""Tests for logging configuration."""

from __future__ import annotations

from arxiv2md_beta.utils.logging_config import configure_logging


def test_configure_logging_returns_path_when_file_enabled(tmp_path):
    """When file logging is enabled, configure_logging returns the log path."""
    log_file = tmp_path / "test.log"
    returned = configure_logging(
        level="INFO",
        log_file=log_file,
        enable_file_logging=True,
    )
    assert returned == log_file
    # The log file is created lazily on the first emit, but the parent dir exists.
    assert log_file.parent.exists()


def test_configure_logging_returns_none_when_file_disabled():
    """When file logging is disabled, configure_logging returns None."""
    returned = configure_logging(
        level="INFO",
        enable_file_logging=False,
    )
    assert returned is None
