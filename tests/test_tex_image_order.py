"""TeX image ordering: title graphics must not shift ar5iv xN positional mapping."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.latex.tex_source import _parse_images_from_tex, _strip_title_blocks_for_image_extraction


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
