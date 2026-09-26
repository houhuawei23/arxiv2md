"""Concurrent-swap safety of the TeX extract cache (Phase 0.2 regression).

The extract dir is shared between concurrent conversions of the same paper.
Info extraction must happen on the task-private staging tree (a concurrent
task's rmtree+rename of the shared path must not be able to pull the tree out
from under our reads), and the returned TexSourceInfo must point at the
canonical cache path, not the since-renamed staging dir.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import arxiv2md_beta.latex.tex_source as tex_source
from arxiv2md_beta.latex.tex_source import extract_local_archive


def _make_zip(archive: Path) -> None:
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("main.tex", "\\documentclass{article}\\begin{document}hi\\end{document}")
        handle.writestr("fig.png", b"\x89PNG fake")


def test_local_extract_reads_staging_and_rebases_info(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "paper.zip"
    _make_zip(archive)
    # output_dir IS the extraction target when passed explicitly (this is how
    # ingestion/local.py calls it: query.cache_dir / "extracted").
    extracted_dir = tmp_path / "out"

    seen_dirs: list[Path] = []
    real_info = tex_source._extract_info_from_dir

    def spy_info(directory: Path) -> tex_source.TexSourceInfo:
        seen_dirs.append(directory)
        return real_info(directory)

    monkeypatch.setattr(tex_source, "_extract_info_from_dir", spy_info)
    info = extract_local_archive(archive, output_dir=extracted_dir)

    # Info was read from the private staging dir, not the shared cache path.
    assert len(seen_dirs) == 1
    assert seen_dirs[0] != extracted_dir
    assert ".tmp-" in seen_dirs[0].name

    # The returned info is re-rooted onto the canonical cache path.
    assert info.extracted_dir == extracted_dir
    assert extracted_dir.is_dir()
    for path in [*info.all_images, *info.image_files.values()]:
        assert extracted_dir in path.parents or path.parent == extracted_dir
        assert path.exists()
        assert seen_dirs[0] not in path.parents
    if info.main_tex_file is not None:
        assert info.main_tex_file.exists()


def test_local_extract_failure_cleans_only_staging(tmp_path: Path, monkeypatch) -> None:
    """A failed extraction must not rmtree the shared dir another task may use."""
    archive = tmp_path / "paper.zip"
    _make_zip(archive)
    extracted_dir = tmp_path / "out" / "extracted"
    extracted_dir.mkdir(parents=True)
    sentinel = extracted_dir / "previous.tex"
    sentinel.write_text("\\documentclass{article}", encoding="utf-8")

    def boom(archive_path: Path, output_dir: Path) -> None:
        raise tex_source.ArchiveExtractionError("simulated corruption")

    monkeypatch.setattr(tex_source, "_extract_zip_archive", boom)
    try:
        extract_local_archive(archive, output_dir=extracted_dir, use_cache=False)
    except tex_source.ArchiveExtractionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ArchiveExtractionError")

    # The pre-existing shared extract survives; only staging debris is gone.
    assert sentinel.exists()
    assert not any(".tmp-" in p.name for p in extracted_dir.parent.iterdir())
