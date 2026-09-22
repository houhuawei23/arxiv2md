"""TeX image ordering: title graphics must not shift ar5iv xN positional mapping."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.latex.tex_source import (
    _parse_images_from_tex,
    _strip_affiliation_blocks_for_image_extraction,
    _strip_title_blocks_for_image_extraction,
)


def test_strip_icmltitle_removes_title_logo_from_includegraphics_order(tmp_path: Path) -> None:
    tex = tmp_path / "main.tex"
    fig = tmp_path / "figures"
    fig.mkdir(parents=True)
    (fig / "logo8.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (fig / "teaser.pdf").write_bytes(b"%PDF-1.4")
    tex.write_text(
        r"""
\documentclass{article}
\begin{document}
\twocolumn[
  \icmltitle{\includegraphics{figures/logo8.png} My Paper Title}
]
\section{Intro}
\begin{figure}
  \includegraphics[width=\columnwidth]{figures/teaser.pdf}
\end{figure}
\end{document}
""",
        encoding="utf-8",
    )
    all_images = list(tmp_path.rglob("*.pdf")) + list(tmp_path.rglob("*.png"))
    m = _parse_images_from_tex(tex, tmp_path, all_images)
    paths = list(m.values())
    assert len(paths) == 1
    assert paths[0].name == "teaser.pdf"


def test_strip_does_not_confuse_icmltitlerunning() -> None:
    s = r"\icmltitlerunning{Short}" + "\n" + r"\icmltitle{\includegraphics{figures/a.png} T}"
    out = _strip_title_blocks_for_image_extraction(s)
    assert "icmltitlerunning" in out
    assert "figures/a.png" not in out


def test_strip_affiliation_removes_institution_logos_from_includegraphics_order(
    tmp_path: Path,
) -> None:
    r"""Fairmeta/NeurIPS-style \\affiliation[...]{\\includegraphics...} must not occupy figure indices."""
    tex = tmp_path / "main.tex"
    fig = tmp_path / "figs"
    fig.mkdir(parents=True)
    for name in ("unc_logo.png", "fig1.pdf"):
        (fig / name).write_bytes(b"%PDF" if name.endswith(".pdf") else b"\x89PNG\r\n\x1a\n")
    tex.write_text(
        r"""
\documentclass{article}
\begin{document}
\title{T}
\affiliation[1]{\includegraphics[width=1em]{figs/unc_logo.png} UNC}
\begin{figure}
  \includegraphics[width=\linewidth]{figs/fig1.pdf}
\end{figure}
\end{document}
""",
        encoding="utf-8",
    )
    all_images = list(tmp_path.rglob("*.pdf")) + list(tmp_path.rglob("*.png"))
    m = _parse_images_from_tex(tex, tmp_path, all_images)
    paths = list(m.values())
    assert len(paths) == 1
    assert paths[0].name == "fig1.pdf"


class TestStripMacroBlockEdges:
    """Characterization for the slice-based rewrite (audit5 X4).

    Length- and offset-preserving replacement must survive nesting, bracket
    groups and unterminated blocks.
    """

    def test_nested_title_inner_not_double_stripped(self) -> None:
        s = r"\title{outer \title{inner} tail} body"
        out = _strip_title_blocks_for_image_extraction(s)
        assert "body" in out
        assert "outer" not in out and "inner" not in out

    def test_offset_preserved_after_strip(self) -> None:
        s = r"AB\icmltitle{logo}CD"
        out = _strip_title_blocks_for_image_extraction(s)
        assert len(out) == len(s)
        assert out.startswith("AB") and out.endswith("CD")

    def test_optional_bracket_group_with_nesting(self) -> None:
        s = r"\title[short [x] long]{real}tail"
        out = _strip_title_blocks_for_image_extraction(s)
        assert "tail" in out and "real" not in out and "short" not in out

    def test_unterminated_brace_left_alone(self) -> None:
        s = r"\title{never closed"
        out = _strip_title_blocks_for_image_extraction(s)
        assert out == s

    def test_affiliation_offset_preserved_and_nested(self) -> None:
        s = r"A\affiliation[a]{inst {x}}B"
        out = _strip_affiliation_blocks_for_image_extraction(s)
        assert len(out) == len(s)
        assert out[0] == "A" and out[-1] == "B"
        assert "inst" not in out

    def test_affiliation_without_block_untouched(self) -> None:
        s = r"\affiliation"
        assert _strip_affiliation_blocks_for_image_extraction(s) == s
