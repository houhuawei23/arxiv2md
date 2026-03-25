"""Logging configuration for arxiv2md-beta."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings


def configure_logging(
    *,
    settings: AppSettings | None = None,
    level: str | None = None,
    log_file: Path | None = None,
    enable_file_logging: bool | None = None,
) -> None:
    """Configure loguru for the application."""
    s = settings or get_settings()
    log = s.logging
    feats = s.features
    eff_level = level if level is not None else s.app.log_level
    eff_file = enable_file_logging if enable_file_logging is not None else feats.enable_file_logging
    eff_log_path = log_file

    logger.remove()

    logger.add(
        sys.stderr,
        format=log.console_format,
        level=eff_level,
        colorize=True,
    )

    if eff_file:
        if eff_log_path is None:
            eff_log_path = s.resolved_default_log_file()
            eff_log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            eff_log_path,
            format=log.file_format,
            level=eff_level,
            rotation=log.file_rotation,
            retention=log.file_retention,
            compression=log.file_compression,
        )


def get_logger():
    """Return the configured loguru logger."""
    return logger
