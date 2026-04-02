"""Tests for TeX author/affiliation parsing and metadata merge."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.latex.author_affiliations import (
    merge_tex_affiliations_into_metadata,
    parse_author_affiliations_from_tex,
)
from arxiv2md_beta.latex.tex_source import TexSourceInfo


def test_parse_icml_icmlauthor_icmlaffiliation() -> None:
    """ICML 2026 style: \\icmlauthor{Name}{keys} and \\icmlaffiliation{key}{Institution}."""
    tex = r"""
\begin{document}
\begin{icmlauthorlist}
\icmlauthor{Xiao Yu}{columbia}
\icmlauthor{Baolin Peng}{msr,projectlead}
\icmlauthor{Ruize Xu}{dartmouth}
\end{icmlauthorlist}
\icmlaffiliation{columbia}{Columbia University, New York}
\icmlaffiliation{msr}{Microsoft Research, Redmond}
\icmlaffiliation{dartmouth}{Dartmouth College, Hanover}
\begin{abstract}
x
\end{abstract}
\end{document}
"""
    out = parse_author_affiliations_from_tex(tex)
    assert len(out) == 3
    assert out[0]["name"] == "Xiao Yu"
    assert "Columbia" in out[0]["affiliations"][0]
    assert "Microsoft" in out[1]["affiliations"][0]
    assert "Dartmouth" in out[2]["affiliations"][0]


def test_parse_iclr_preamble_author_superscript_legend() -> None:
    """ICLR/NeurIPS: \\author in preamble before \\begin{document}, superscript + legend line."""
    tex = r"""
\documentclass{article}
\author{
Yanjiang Guo$^{*12}$, Lucy Xiaoyang Shi$^{*1}$, Jianyu Chen$^{2}$, Chelsea Finn$^{1}$ \\
$^{*}$ Equal Contribution, $^{1}$ Stanford University, $^{2}$ Tsinghua University \\
Project page: \url{https://example.com}
}
\begin{document}
\maketitle
\begin{abstract}
x
\end{abstract}
\end{document}
"""
    out = parse_author_affiliations_from_tex(tex)
    assert len(out) == 4
    assert out[0]["name"] == "Yanjiang Guo"
    assert set(out[0]["affiliations"]) == {"Stanford University", "Tsinghua University"}
    assert out[1]["affiliations"] == ["Stanford University"]
    assert out[2]["affiliations"] == ["Tsinghua University"]
    assert out[3]["affiliations"] == ["Stanford University"]


def test_parse_ieee_tran_comma_separated_author_line() -> None:
    """IEEEtran: multiple authors in one ``\\author{ A, B, C }`` with ``\\thanks`` (no affiliations in TeX)."""
    tex = r"""
\documentclass[lettersize,journal]{IEEEtran}
\begin{document}
\author{Mengyuan Liu, Juyi Sheng$^{\ast}$, Peiming Li, Ziyi Wang, Tianming Xu, Tiantian Xu, Hong Liu$^{\ast}$
\thanks{$^{\ast}$Corresponding author.}
}
\maketitle
\begin{abstract}
x
\end{abstract}
\end{document}
"""
    out = parse_author_affiliations_from_tex(tex)
    assert len(out) == 7
    assert out[0]["name"] == "Mengyuan Liu"
    assert out[1]["name"] == "Juyi Sheng"
    assert out[6]["name"] == "Hong Liu"
    assert all(not a.get("affiliations") for a in out)


def test_parse_ieee_two_blocks() -> None:
    tex = r"""
\documentclass{ieee}
\begin{document}
\IEEEauthorblockN{Alice Example}
\IEEEauthorblockA{MIT \\ Cambridge, MA}
\IEEEauthorblockN{Bob Smith}
\IEEEauthorblockA{Stanford University}
\begin{abstract}
Hi
\end{abstract}
\end{document}
"""
    out = parse_author_affiliations_from_tex(tex)
    assert len(out) == 2
    assert out[0]["name"] == "Alice Example"
    assert any("MIT" in a for a in out[0]["affiliations"])
    assert out[1]["name"] == "Bob Smith"
    assert any("Stanford" in a for a in out[1]["affiliations"])


def test_parse_sequential_author_affiliation() -> None:
    tex = r"""
\begin{document}
\author{Jane Doe}
\affiliation{%
  \institution{ACME University}
}
\begin{abstract}
x
\end{abstract}
\end{document}
"""
    out = parse_author_affiliations_from_tex(tex)
    assert len(out) >= 1
    assert out[0]["name"] == "Jane Doe"
    assert out[0]["affiliations"]
    assert any("ACME" in a for a in out[0]["affiliations"])


def test_merge_tex_affiliations_into_metadata(tmp_path: Path) -> None:
    main = tmp_path / "main.tex"
    main.write_text(
        r"""
\documentclass{article}
\begin{document}
\author{Jane Doe}
\affiliation{%
  \institution{ACME University}
}
\begin{abstract}
x
\end{abstract}
\end{document}
""",
        encoding="utf-8",
    )
    info = TexSourceInfo(
        extracted_dir=tmp_path,
        main_tex_file=main,
        image_files={},
        all_images=[],
    )
    metadata: dict = {"authors": [{"name": "Jane Doe"}]}
    n = merge_tex_affiliations_into_metadata(metadata, info)
    assert n == 1
    assert metadata["authors"][0].get("affiliations")
    assert any("ACME" in x for x in metadata["authors"][0]["affiliations"])


def test_merge_no_main_tex_returns_zero() -> None:
    info = TexSourceInfo(
        extracted_dir=Path("/tmp"),
        main_tex_file=None,
        image_files={},
        all_images=[],
    )
    metadata: dict = {"authors": [{"name": "A"}]}
    assert merge_tex_affiliations_into_metadata(metadata, info) == 0
