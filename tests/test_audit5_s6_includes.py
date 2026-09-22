"""audit5 S6 PR6.3 (X2): one include expansion per paper + prebuilt name index.

The include tree was expanded three times per paper (image parse, figure-env
parse, author/affiliation parse), and every unresolved include triggered a
full-directory rglob. Expansion is now memoized per (main tex, base dir) and
both resolvers share a single implementation with a prebuilt filename index.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.latex import includes, tex_source


def _make_tree(tmp_path: Path) -> Path:
    base = tmp_path / "src"
    base.mkdir()
    (base / "main.tex").write_text("\\input{sec}\n\\includegraphics{fig.png}\n", encoding="utf-8")
    (base / "sec.tex").write_text("SECTION \\input{missing_file}\n", encoding="utf-8")
    (base / "fig.png").write_bytes(b"png")
    # Decoy files make the old per-miss rglob scans measurable.
    for i in range(30):
        (base / f"decoy{i}.txt").write_text("x", encoding="utf-8")
    return base


def _info(base: Path) -> tex_source.TexSourceInfo:
    return tex_source.TexSourceInfo(
        extracted_dir=base,
        main_tex_file=base / "main.tex",
        image_files={},
        all_images=[base / "fig.png"],
        figure_image_files=[],
    )


@pytest.fixture()
def _count_expansion(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = includes._resolve_includes_recursive

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(includes, "_resolve_includes_recursive", counting)
    return calls


class TestSharedExpansion:
    def test_three_parse_paths_expand_once(self, tmp_path: Path, _count_expansion: list[int]) -> None:
        """One expansion serves image parse + figure-env parse + affiliation parse."""
        base = _make_tree(tmp_path)
        tex_source._parse_images_from_tex(base / "main.tex", base, [base / "fig.png"])
        tex_source._parse_figure_env_images_from_tex(base / "main.tex", base, [base / "fig.png"])
        tex_source.expand_tex_source_for_parsing(_info(base))
        # main.tex + sec.tex visited once each; the old code re-expanded 3×.
        assert len(_count_expansion) == 2, len(_count_expansion)

    def test_expansion_result_consistent_across_consumers(self, tmp_path: Path) -> None:
        base = _make_tree(tmp_path)
        images = tex_source._parse_images_from_tex(base / "main.tex", base, [base / "fig.png"])
        assert list(images.values()) == [base / "fig.png"]
        expanded = tex_source.expand_tex_source_for_parsing(_info(base))
        assert "SECTION" in expanded and "\\input" not in expanded

    def test_cache_revalidates_on_new_extract(self, tmp_path: Path, _count_expansion: list[int]) -> None:
        """A changed base dir (fresh extract) must not serve a stale expansion."""
        import os
        import time

        base = _make_tree(tmp_path)
        tex_source.expand_tex_source_for_parsing(_info(base))
        # Simulate a fresh extract: same path, new content, bumped mtime.
        time.sleep(0.01)
        (base / "sec.tex").write_text("CHANGED", encoding="utf-8")
        os.utime(base, ns=(time.time_ns(), time.time_ns()))
        tex_source.expand_tex_source_for_parsing(_info(base))
        assert len(_count_expansion) == 4  # 2 files × 2 expansions


class TestFilenameIndex:
    def test_missing_include_does_not_rglob_per_miss(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        base = _make_tree(tmp_path)
        patterns: list[str] = []
        real = Path.rglob

        def counting(self: Path, pattern: str):
            patterns.append(pattern)
            return real(self, pattern)

        monkeypatch.setattr(Path, "rglob", counting)
        tex_source._parse_images_from_tex(base / "main.tex", base, [base / "fig.png"])
        # One rglob("*") builds the index; per-miss pattern scans are gone.
        assert [p for p in patterns if p != "*"] == [], patterns

    def test_subdir_include_still_resolves(self, tmp_path: Path) -> None:
        base = tmp_path / "src"
        base.mkdir()
        (base / "sub").mkdir()
        (base / "main.tex").write_text("\\input{tables/nested}\n", encoding="utf-8")
        (base / "tables").mkdir()
        (base / "tables" / "nested.tex").write_text("NESTED", encoding="utf-8")
        out = tex_source.expand_tex_source_for_parsing(_info(base))
        assert "NESTED" in out

    def test_full_resolver_behavior_unchanged(self, tmp_path: Path) -> None:
        """resolve_latex_includes keeps its diamond + circular semantics."""
        base = tmp_path / "src"
        base.mkdir()
        (base / "main.tex").write_text("\\input{a}\n\\input{a}\n", encoding="utf-8")
        (base / "a.tex").write_text("A \\input{a_loop}", encoding="utf-8")
        (base / "a_loop.tex").write_text("L \\input{a}", encoding="utf-8")
        out = includes.resolve_latex_includes(base / "main.tex", base)
        # Diamond: both \input{a} expand; the cycle breaks inside the second.
        assert out.count("A ") >= 1
