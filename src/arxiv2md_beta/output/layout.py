"""Output directory layout and sanitized basenames for papers (no CLI)."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings

# Schemes that emit fixed internal filenames (paper.md, References.md,
# Appendix.md, <dir>.pdf) instead of stem-prefixed ones. Single source of
# truth for both directory layout and file finalization.
FIXED_INTERNAL_SCHEMES = frozenset({"paper-pipeline", "arxiv-ym"})


def determine_output_dir(output: str | None, settings: AppSettings | None = None) -> Path:
    """Resolve base output directory from CLI string or config default.

    ``~`` is expanded explicitly: Typer passes the raw string through, so a
    quoted ``-o "~/papers"`` (or a path coming from a batch file / the
    ``cli_defaults.output_dir`` setting) would otherwise create a literal
    ``~`` directory relative to the cwd.
    """
    s = settings or get_settings()
    if output:
        return Path(output).expanduser()
    return Path(s.cli_defaults.output_dir).expanduser()


def _sanitize_for_filesystem(s: str, max_length: int = 220) -> str:
    """Sanitize a string for use in file/directory names (alphanumeric, hyphen, underscore only)."""
    if not s:
        return ""
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in s)
    safe = safe.strip().replace(" ", "-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-")
    if len(safe) > max_length:
        truncated = safe[:max_length]
        last_hyphen = truncated.rfind("-")
        safe = truncated[:last_hyphen] if last_hyphen > max_length * 0.8 else truncated
    return safe


def sanitize_title_for_filesystem(title: str, max_length: int = 220, *, settings: AppSettings | None = None) -> str:
    """Sanitize title for use in file/directory names."""
    s = settings or get_settings()
    unknown = s.output_naming.default_unknown_title
    if not title:
        return unknown
    return _sanitize_for_filesystem(title, max_length)


def _compose_basename(
    scheme: str,
    date_str: str,
    safe_source: str,
    safe_short: str,
    safe_title: str,
) -> str:
    """Join the 3-4 segments in the order the naming scheme dictates."""
    if scheme == "paper-pipeline":
        if safe_short:
            return f"{safe_source}-{date_str}-{safe_short}-{safe_title}"
        return f"{safe_source}-{date_str}-{safe_title}"
    # classic and arxiv-ym share the same segment order: [date]-[source]-[short]-[title]
    if safe_short:
        return f"{date_str}-{safe_source}-{safe_short}-{safe_title}"
    return f"{date_str}-{safe_source}-{safe_title}"


def _date_segment(
    scheme: str,
    submission_date: str | None,
    *,
    fallback: str,
) -> str:
    """Render the date segment per scheme.

    classic / paper-pipeline use the full YYYYMMDD submission date.
    arxiv-ym truncates it to YYYYMM (e.g. 20260603 -> 202606) so the prefix
    encodes the arXiv id's year-month.
    """
    if scheme == "arxiv-ym" and submission_date and len(submission_date) == 8 and submission_date.isdigit():
        return submission_date[:6]
    return submission_date if submission_date else fallback


def build_output_basename(
    submission_date: str | None,
    title: str | None,
    source: str = "Arxiv",
    short: str | None = None,
    *,
    max_title_length: int | None = None,
    max_basename_length: int | None = None,
    naming_scheme: str | None = None,
    settings: AppSettings | None = None,
) -> str:
    """Build output basename for directory and files.

    classic:        [Date]-[Source]-[Short]-[Paper Name]   (Date = full YYYYMMDD)
    paper-pipeline: [Source]-[Date]-[Short]-[Paper Name]   (Date = full YYYYMMDD)
    arxiv-ym:       [YYYYMM]-[Source]-[Short]-[Paper Name] (YYYYMM, e.g. 202606)
    """
    s = settings or get_settings()
    on = s.output_naming
    scheme = naming_scheme if naming_scheme is not None else on.naming_scheme
    max_title_length = max_title_length if max_title_length is not None else on.max_title_length
    max_basename_length = max_basename_length if max_basename_length is not None else on.max_basename_length

    date_str = _date_segment(scheme, submission_date, fallback=on.default_unknown_title)
    src_max = on.sanitize_source_max_length
    short_max = on.sanitize_short_max_length
    safe_source = _sanitize_for_filesystem(source, max_length=src_max) or s.cli_defaults.source
    safe_short = _sanitize_for_filesystem(short, max_length=short_max) if short else ""
    safe_title = sanitize_title_for_filesystem(
        title or on.default_unknown_title, max_length=max_title_length, settings=s
    )

    basename = _compose_basename(scheme, date_str, safe_source, safe_short, safe_title)

    if len(basename) > max_basename_length:
        # Fixed (non-title) prefix; shrink the title to fit max_basename_length.
        if safe_short:
            fixed_part = (
                f"{safe_source}-{date_str}-{safe_short}-"
                if scheme == "paper-pipeline"
                else f"{date_str}-{safe_source}-{safe_short}-"
            )
        else:
            fixed_part = f"{safe_source}-{date_str}-" if scheme == "paper-pipeline" else f"{date_str}-{safe_source}-"
        max_title_in_basename = max_basename_length - len(fixed_part)
        if len(safe_title) > max_title_in_basename:
            safe_title = sanitize_title_for_filesystem(
                title or on.default_unknown_title,
                max_length=max_title_in_basename,
                settings=s,
            )
        basename = _compose_basename(scheme, date_str, safe_source, safe_short, safe_title)

    return basename


def create_paper_output_dir(
    base_output_dir: Path,
    submission_date: str | None,
    title: str | None,
    source: str = "Arxiv",
    short: str | None = None,
    *,
    settings: AppSettings | None = None,
    identity: str | None = None,
) -> Path:
    """Create output directory for paper with format [date]-[source]-[short]-[title]."""
    s = settings or get_settings()
    dir_name = build_output_basename(submission_date, title, source, short, settings=s)
    return _claim_paper_output_dir(base_output_dir, dir_name, identity)


def _claim_paper_output_dir(base_output_dir: Path, dir_name: str, identity: str | None) -> Path:
    """Claim a paper directory via exclusive marker creation.

    Concurrent conversions whose names sanitize to the same directory name
    (e.g. the same paper's v1 and v2 — distinct identities by design) must
    not share a directory. The marker is created with ``O_EXCL`` semantics:
    the winner keeps the plain name, the loser retries under a deterministic
    collision suffix instead of overwriting the winner's outputs. Re-running
    the same identity reuses its directory (idempotency contract).
    """
    candidate_name = dir_name
    for _ in range(4):
        output_dir = base_output_dir / candidate_name
        output_dir.mkdir(parents=True, exist_ok=True)
        marker = output_dir / ".arxiv2md-paper"
        if identity is None:
            return output_dir
        try:
            with marker.open("x", encoding="utf-8") as f:
                f.write(identity + "\n")
            return output_dir
        except FileExistsError:
            existing = marker.read_text(encoding="utf-8", errors="replace").strip()
            if existing in ("", identity):
                # Empty marker: a crashed writer (or an unrelated empty dir) —
                # adopt it, mirroring find_completed_output_dir semantics,
                # and record our identity for future collision checks.
                if not existing:
                    marker.write_text(identity + "\n", encoding="utf-8")
                return output_dir
        candidate_name = f"{dir_name}-{_stable_collision_suffix(f'{identity}:{candidate_name}')}"
    # Every candidate occupied by other papers — deterministic last resort.
    assert identity is not None  # narrowed by every loop path taken here
    return base_output_dir / f"{dir_name}-{_stable_collision_suffix(identity)}"


def _stable_collision_suffix(identity: str) -> str:
    """Generate a deterministic short suffix for an occupied output name."""
    import hashlib

    return hashlib.sha256(identity.encode()).hexdigest()[:8]


def identity_for_arxiv_id(arxiv_id: str) -> str:
    """Canonical identity written to ``.arxiv2md-paper`` for arXiv inputs.

    Must stay in sync with the ``identity=`` values passed at the ingestion
    call sites (orchestrator / latex / local / local_html) — the idempotency
    pre-check matches against exactly these strings.
    """
    return arxiv_id


def identity_for_local_path(path) -> str:
    """Canonical identity for local HTML/archive inputs (resolved path)."""
    return str(Path(path).resolve())


def find_completed_output_dir(base_output_dir: Path, identity: str) -> Path | None:
    """Return the output directory where ``identity`` was already converted.

    A directory counts as completed when its ``.arxiv2md-paper`` marker exists
    and matches ``identity`` (an empty marker counts as a match) and the
    directory contains at least one non-empty ``.md`` file. Directories
    without a marker are never adopted — an unrelated directory that happens
    to contain Markdown must not swallow a conversion. Returns None otherwise.

    Used by the convert runner to skip re-ingestion (resume / idempotency);
    ``--force`` bypasses the check.
    """
    if not base_output_dir.exists():
        return None
    for entry in base_output_dir.iterdir():
        if not entry.is_dir():
            continue
        marker = entry / ".arxiv2md-paper"
        if not marker.exists():
            continue
        existing = marker.read_text(encoding="utf-8", errors="replace").strip()
        if existing and existing != identity:
            continue
        if _has_nonempty_markdown(entry):
            return entry
    return None


def _has_nonempty_markdown(directory: Path) -> bool:
    """True when ``directory`` contains at least one ``.md`` file with size > 0.

    ``.part`` residues are ignored: they are the in-flight siblings of atomic
    writes, so a torn transfer must never satisfy the idempotency check.
    """
    try:
        return any(
            p.is_file() and p.suffix.lower() == ".md" and not p.name.endswith(".part") and p.stat().st_size > 0
            for p in directory.iterdir()
        )
    except OSError:
        return False


def determine_images_dir(settings: AppSettings | None = None) -> str:
    """Return configured images subdirectory name (e.g. ``images``)."""
    s = settings or get_settings()
    return s.cli_defaults.images_subdir
