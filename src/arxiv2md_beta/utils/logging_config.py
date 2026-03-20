"""Logging configuration for arxiv2md-beta."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def configure_logging(
    *,
    level: str = "INFO",
    log_file: Path | None = None,
    enable_file_logging: bool = False,
) -> None:
    """Configure loguru for the application.

    Parameters
    ----------
    level : str
        Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    log_file : Path | None
        Path to log file. If None, uses default location.
    enable_file_logging : bool
        Whether to enable file logging.
    """
    # Remove default handler
    logger.remove()

    # Add console handler with color
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=level,
        colorize=True,
    )

    # Add file handler if enabled
    if enable_file_logging:
        if log_file is None:
            log_file = Path.home() / ".arxiv2md_beta" / "arxiv2md_beta.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level=level,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
        )


def get_logger():
    """Get a configured logger instance.

    Returns
    -------
    Logger
        Configured logger instance
    """
    return logger


# Configure default logging
configure_logging()
