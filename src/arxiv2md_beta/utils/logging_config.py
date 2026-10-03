"""Logging configuration for arxiv2md-beta.

Per-run file logging: each CLI invocation writes its own log file under
``<cache>/logs/<YYYY-MM-DD>/<YYYY-MM-DD-HH-MM-SS>-<subcommand>-<label>.log``.
Every record carries the wall-clock time, level, and elapsed time since
process start; the run itself is bracketed by a header (full command line,
version, pid) and a footer (result, exit code, total elapsed).
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from loguru import logger
from loguru._logger import Logger

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings

if TYPE_CHECKING:
    from types import TracebackType

# ── Run state (single CLI process = single run) ──────────────────────────────

_run_started_at: datetime | None = None
_run_log_file: Path | None = None

# Options that consume the next argv token as their value — skipped when
# deriving the run label so `-o outputs` doesn't label the log "outputs".
_VALUE_OPTS = {
    "--config",
    "--env",
    "-E",
    "--output",
    "-o",
    "--parser",
    "--source",
    "--short",
    "--sections",
    "--section",
    "--section-filter-mode",
    "--naming-scheme",
    "--concurrency",
    "-c",
}


def sanitize_label(raw: str, *, max_length: int = 60) -> str:
    """Reduce an argv token to a filesystem-safe log label."""
    if "://" in raw:
        raw = urlsplit(raw).path.rstrip("/").rsplit("/", 1)[-1] or raw
    raw = Path(raw).name if "/" in raw or raw.endswith(("~",)) else raw
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    return cleaned[:max_length] or "run"


def resolve_run_label(argv: list[str], subcommand: str | None) -> str:
    """Derive the run-instance label from argv: the first positional token.

    For ``convert 2501.11120 -o out`` the label is the arXiv id; for ``batch
    inputs.txt`` the list filename. Falls back to ``"run"`` when argv has no
    discernible positional (help / version / config subcommands).
    """
    if not subcommand:
        return "cli"
    try:
        idx = argv.index(subcommand)
    except ValueError:
        return "run"
    tokens = argv[idx + 1 :]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            if "=" in tok or tok not in _VALUE_OPTS:
                i += 1  # flag without separate value (--no-images, --config=x)
            else:
                i += 2  # value-taking option: skip its value too
            continue
        return sanitize_label(tok)
    return "run"


# ── Per-run file sink ────────────────────────────────────────────────────────

_LEVEL_WIDTH = 8


def _make_run_file_sink(start: datetime, fmt: str, path: Path) -> Any:
    """Build a loguru sink writing ``fmt`` (with an ``{elapsed}`` placeholder).

    The sink owns its file handle — loguru function sinks are fully
    responsible for output (nothing is written unless the sink writes it).
    Loguru serializes sink calls with a lock, so a shared handle is safe.
    """

    def _elapsed_str(record_time: datetime) -> str:
        delta = record_time - start
        total = delta.total_seconds()
        h, rem = divmod(int(total), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}.{delta.microseconds // 1000:03d}"

    handle = path.open("a", encoding="utf-8")

    def sink(message: Any) -> None:
        record = message.record
        line = (
            fmt.replace("{time:YYYY-MM-DD HH:mm:ss}", record["time"].strftime("%Y-%m-%d %H:%M:%S"))
            .replace("{level: <8}", f"{record['level'].name:<{_LEVEL_WIDTH}}")
            .replace("{elapsed}", _elapsed_str(record["time"]))
            .replace("{message}", str(record["message"]))
            + "\n"
        )
        exc: tuple[type[BaseException], BaseException, TracebackType] | None = record.get("exception")
        if exc is not None:
            line += "".join(traceback.format_exception(*exc))
        handle.write(line)
        handle.flush()

    return sink


def _cleanup_old_day_dirs(logs_dir: Path, retention_days: int, now: datetime) -> None:
    """Delete ``YYYY-MM-DD`` day directories older than the retention window."""
    if retention_days <= 0 or not logs_dir.is_dir():
        return
    cutoff = (now - timedelta(days=retention_days)).date()
    for entry in logs_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            day = datetime.strptime(entry.name, "%Y-%m-%d").date()
        except ValueError:
            continue  # not a day directory — leave it alone
        if day < cutoff:
            # best effort; next startup retries
            with contextlib.suppress(OSError):
                shutil.rmtree(entry)


# ── Public API ───────────────────────────────────────────────────────────────


def configure_logging(
    *,
    settings: AppSettings | None = None,
    level: str | None = None,
    log_file: Path | None = None,
    enable_file_logging: bool | None = None,
    subcommand: str | None = None,
) -> Path | None:
    """Configure loguru for the application.

    When per-run file logging is on (the default), creates
    ``<logs_dir>/<today>/<HH-MM-SS>-<subcommand>-<label>.log``, writes a run
    header (command line, version, pid, start time) and stores the run start
    so :func:`log_run_result` can close the run with its result.

    Returns:
    -------
    Path | None
        Path to the log file if file logging is enabled, otherwise ``None``.
    """
    global _run_started_at, _run_log_file

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
        started = datetime.now().astimezone()  # aware: loguru record times are aware
        _run_started_at = started
        if eff_log_path is None:
            eff_log_path = _per_run_log_path(s, started, subcommand)
        if eff_log_path:
            eff_log_path.parent.mkdir(parents=True, exist_ok=True)
            if log.per_run_files:
                logger.add(
                    _make_run_file_sink(started, log.run_file_format, eff_log_path),
                    level=eff_level,
                )
            else:  # legacy single rotating file
                logger.add(
                    eff_log_path,
                    format=log.file_format,
                    level=eff_level,
                    rotation=log.file_rotation,
                    retention=log.file_retention,
                    compression=log.file_compression,
                )
            _run_log_file = eff_log_path
            _log_run_header(s)

    return eff_log_path if eff_file else None


def _per_run_log_path(s: AppSettings, started: datetime, subcommand: str | None) -> Path:
    day = started.strftime("%Y-%m-%d")
    stamp = started.strftime("%Y-%m-%d-%H-%M-%S")
    label = resolve_run_label(sys.argv[1:], subcommand)
    cmd = subcommand or "cli"
    logs_dir = s.resolved_logs_dir()
    if log_per_run_enabled(s):
        _cleanup_old_day_dirs(logs_dir, s.logging.retention_days, started)
    return logs_dir / day / f"{stamp}-{cmd}-{label}.log"


def log_per_run_enabled(s: AppSettings) -> bool:
    return s.logging.per_run_files


def _log_run_header(s: AppSettings) -> None:
    from arxiv2md_beta import __version__

    logger.info(f"Run started | command: {shlex.join(sys.argv)}")
    logger.info(
        f"Run context | version={__version__} python={sys.version.split()[0]} pid={os.getpid()} cwd={Path.cwd()}"
    )


def log_run_result(*, status: str, exit_code: int) -> None:
    """Close the run with its result; no-op before logging is configured."""
    if _run_started_at is None:
        return
    delta = datetime.now().astimezone() - _run_started_at
    logger.info(f"Run finished | result={status} exit_code={exit_code} elapsed={delta.total_seconds():.2f}s")
    _ = _run_log_file  # kept for tests / future consumers


def get_log_file() -> Path | None:
    """Path of this run's log file, or ``None`` when file logging is off."""
    return _run_log_file


def reset_run_state() -> None:
    """Clear run state (test isolation)."""
    global _run_started_at, _run_log_file
    _run_started_at = None
    _run_log_file = None


def get_logger() -> Logger:
    """Return the configured loguru logger (loguru ``logger`` singleton)."""
    return cast("Logger", logger)
