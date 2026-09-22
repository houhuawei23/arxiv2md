"""audit5 G3-1: same-stem images must not race to write one output file.

TeX trees commonly ship figures with identical basenames in different
subdirectories. Per-worker stem-derived names made them overwrite each
other (torn PNG under concurrency) and the last writer silently won the
stem→image mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.images.processor import (
    _atomic_write,
    _process_single_image,
    _unique_output_names,
)


class TestUniqueOutputNames:
    def test_same_stem_different_dirs_get_suffixes(self):
        a = Path("figures/fig.png")
        b = Path(" appendix/fig.png")
        names = _unique_output_names([a, b])
        assert names[a] == "fig.png"
        assert names[b] == "fig_1.png"

    def test_pdf_and_raster_collide_on_png(self):
        pdf = Path("f/fig.pdf")
        raster = Path("g/fig.png")
        names = _unique_output_names([pdf, raster])
        assert names[pdf] == "fig.png"
        assert names[raster] == "fig_1.png"

    def test_unique_names_stay_untouched(self):
        a = Path("f/a.png")
        b = Path("g/b.png")
        names = _unique_output_names([a, b])
        assert names == {a: "a.png", b: "b.png"}

    def test_three_way_collision_counts_up(self):
        paths = [Path(f"d{i}/fig.png") for i in range(3)]
        names = _unique_output_names(paths)
        assert [names[p] for p in paths] == ["fig.png", "fig_1.png", "fig_2.png"]


class TestAtomicWrite:
    def test_writes_content_and_leaves_no_tmp(self, tmp_path: Path):
        dest = tmp_path / "out.png"

        def write(p: Path) -> None:
            p.write_bytes(b"PNGDATA")

        _atomic_write(write, dest)
        assert dest.read_bytes() == b"PNGDATA"
        assert [f.name for f in tmp_path.iterdir()] == ["out.png"]

    def test_failure_cleans_temp_and_propagates(self, tmp_path: Path):
        dest = tmp_path / "out.png"

        def bad(_p: Path) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            _atomic_write(bad, dest)
        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []


class TestAssignedNameProcessing:
    def test_raster_copied_under_assigned_name(self, tmp_path: Path):
        src_dir = tmp_path / "sub"
        src_dir.mkdir()
        src = src_dir / "fig.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        out_dir = tmp_path / "images"
        out_dir.mkdir()

        rel, stem = _process_single_image(
            src, out_dir, 0, assigned_name="fig_1.png", dpi=150, trim_whitespace=False, trim_tolerance=10
        )
        assert (out_dir / "fig_1.png").read_bytes() == src.read_bytes()
        assert rel == Path("images/fig_1.png")
        assert stem == "fig"
