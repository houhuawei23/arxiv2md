"""Resolve output paths for ``paper.yml`` with optional numeric suffix when the file exists."""

from __future__ import annotations

from pathlib import Path


def resolve_paper_yml_output_path(requested: Path, *, force: bool = False) -> Path:
    """Return a path to write; if ``requested`` exists and ``force`` is False, use ``stem.N.suffix``.

    Examples
    --------
    - ``out/paper.yml`` missing → ``out/paper.yml``
    - ``out/paper.yml`` exists → ``out/paper.1.yml``, then ``paper.2.yml``, …
    - Directory ``out/`` → ``out/paper.yml`` (then increment if needed)
    """
    p = Path(requested).expanduser()
    if p.is_dir():
        p = p / "paper.yml"
    elif p.suffix.lower() not in (".yml", ".yaml"):
        p = p / "paper.yml"
    p = p.resolve()
    if force or not p.exists():
        return p
    parent = p.parent
    stem = p.stem
    suffix = p.suffix if p.suffix else ".yml"
    n = 1
    while True:
        cand = parent / f"{stem}.{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1
