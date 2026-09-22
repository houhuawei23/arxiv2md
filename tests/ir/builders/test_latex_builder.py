"""Tests for LaTeXBuilder."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    LaTeXBuilder,
    MarkdownEmitter,
)

pypandoc = pytest.importorskip("pypandoc")


class TestLaTeXBuilder:
    """Tests for LaTeXBuilder: Pandoc JSON AST -> DocumentIR."""

    def test_empty_document(self):
        """Empty LaTeX produces minimal DocumentIR."""
        builder = LaTeXBuilder()
        doc = builder.build("", arxiv_id="test")
        assert isinstance(doc, DocumentIR)
        assert doc.metadata.parser == "latex"
        assert doc.metadata.arxiv_id == "test"
        assert doc.sections == []

    def test_simple_paragraph(self):
        """A simple paragraph."""
        tex = r"""\documentclass{article}
\begin{document}
Hello world.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_title_author_abstract(self):
        """Title, author, and abstract in LaTeX preamble."""
        tex = r"""\documentclass{article}
\title{My Great Paper}
\author{Alice \and Bob}
\begin{document}
\maketitle
\begin{abstract}
This is the abstract text.
\end{abstract}
\section{Introduction}
Main content here.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert doc.metadata.title is not None
        assert "My Great Paper" in (doc.metadata.title or "")

    def test_title_from_meta_when_provided(self):
        """Pre-extracted title/author/abstract are forwarded."""
        builder = LaTeXBuilder()
        doc = builder.build(
            r"\documentclass{article}\begin{document}Hello\end{document}",
            arxiv_id="test",
            title="Pre Title",
            authors=["Author One"],
            abstract="Pre Abstract",
        )
        assert doc.metadata.title == "Pre Title"
        assert [a.name for a in doc.metadata.authors] == ["Author One"]
        assert doc.metadata.abstract_text == "Pre Abstract"

    def test_sections(self):
        """Sections are extracted from LaTeX."""
        tex = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Intro content.
\section{Methods}
Methods content.
\subsection{Settings}
Settings content.
\section{Conclusion}
Conclusion.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1
        titles = [s.title for s in doc.sections]
        assert any("Introduction" in t or "intro" in t.lower() for t in titles)

    def test_emphasis_inline(self):
        """Emphasis and strong text."""
        tex = r"""\documentclass{article}
\begin{document}
Some \emph{italic} and \textbf{bold} text.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        # Should parse without error
        assert len(doc.sections) >= 1

    def test_math_inline_and_display(self):
        """Inline math and display math."""
        tex = r"""\documentclass{article}
\begin{document}
Inline $x^2 + y^2 = z^2$ math.
Display:
\[
\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}
\]
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_lists(self):
        """Ordered and unordered lists."""
        tex = r"""\documentclass{article}
\begin{document}
\begin{itemize}
\item First
\item Second
\end{itemize}
\begin{enumerate}
\item Step one
\item Step two
\end{enumerate}
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_code_block(self):
        """Verbatim code block."""
        tex = r"""\documentclass{article}
\begin{document}
\begin{verbatim}
def hello():
    print("world")
\end{verbatim}
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_image_with_map(self):
        """Images are resolved via image_map."""
        from pathlib import Path

        tex = r"""\documentclass{article}
\begin{document}
\includegraphics{fig1.pdf}
\end{document}"""
        builder = LaTeXBuilder(image_map={"fig1.pdf": Path("./images/fig1.png")})
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_image_without_map(self):
        """Images without map keep original src."""
        tex = r"""\documentclass{article}
\begin{document}
\includegraphics{unknown.pdf}
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_bibliography_detection(self):
        """Bibliography section is separated."""
        tex = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Content.
\begin{thebibliography}{9}
\bibitem{ref1} Reference one.
\bibitem{ref2} Reference two.
\end{thebibliography}
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        # The bibliography is detected and separated
        assert isinstance(doc, DocumentIR)

    def test_arxiv_id_propagated(self):
        """ArXiv ID is propagated to metadata."""
        builder = LaTeXBuilder()
        doc = builder.build(r"\documentclass{article}\begin{document}Hi\end{document}", arxiv_id="2501.12345v2")
        assert doc.metadata.arxiv_id == "2501.12345v2"

    def test_parser_label(self):
        """Metadata parser label is 'latex'."""
        builder = LaTeXBuilder()
        doc = builder.build(r"\documentclass{article}\begin{document}Hi\end{document}", arxiv_id="test")
        assert doc.metadata.parser == "latex"

    def test_roundtrip_via_markdown(self):
        """LaTeX -> DocumentIR -> Markdown roundtrip produces sensible output."""
        tex = r"""\documentclass{article}
\title{Test Paper}
\author{Author Name}
\begin{document}
\maketitle
\begin{abstract}
An abstract.
\end{abstract}
\section{Introduction}
This is introduction text with \emph{emphasis} and \textbf{bold}.
\section{Methods}
We use $E=mc^2$ to derive results.
\begin{itemize}
\item Item A
\item Item B
\end{itemize}
\section{Conclusion}
Final thoughts.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test-roundtrip")

        emitter = MarkdownEmitter()
        md = emitter.emit(doc)
        assert len(md) > 0
        # The markdown should contain some recognizable text
        assert "Introduction" in md or "intro" in md.lower()
        assert "emphas" in md.lower() or "italic" in md.lower()  # might be *emphasis*
        assert "bold" in md.lower()

    def test_figure_in_document(self):
        """Figure environment is converted."""
        tex = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\begin{figure}
\includegraphics{plot.pdf}
\caption{A test figure.}
\label{fig:test}
\end{figure}
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_table_in_document(self):
        """Table environment is converted."""
        tex = r"""\documentclass{article}
\begin{document}
\begin{table}
\begin{tabular}{l c r}
Left & Center & Right \\
1 & 2 & 3 \\
\end{tabular}
\caption{A test table.}
\label{tab:test}
\end{table}
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_link_in_document(self):
        """URL links are preserved."""
        tex = r"""\documentclass{article}
\usepackage{hyperref}
\begin{document}
See \url{https://example.com} for details.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_quoted_text(self):
        """Quoted text."""
        tex = r"""\documentclass{article}
\begin{document}
He said ``hello world'' to everyone.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1

    def test_footnote_converted_to_blocks(self):
        r"""\footnote{...} is converted to superscript marker + footnote blocks."""
        tex = r"""\documentclass{article}
\begin{document}
Hello world\footnote{This is a footnote.}.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1
        # Check that footnote counter was incremented
        assert builder._footnote_counter >= 1
        # The markdown output should contain the footnote marker
        emitter = MarkdownEmitter()
        md = emitter.emit(doc)
        # Regression A13: footnote marker must render as [^N] via a semantic
        # LinkIR(kind="footnote"), not a baked-in TextIR.
        assert "[^1]" in md

    def test_citation_converted_to_markers(self):
        r"""\cite{...} is converted to superscript citation markers."""
        tex = r"""\documentclass{article}
\begin{document}
As shown previously \cite{smith2020,jones2021}.
\end{document}"""
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="test")
        assert len(doc.sections) >= 1
        emitter = MarkdownEmitter()
        md = emitter.emit(doc)
        # Should contain citation markers
        assert "smith2020" in md or "1" in md


class TestLaTeXBuilderRegressions:
    """Regression tests for IR-builder fixes (A12/A14)."""

    def test_smallcaps_renders_as_plain_text_not_italic(self):
        r"""\textsc{...} must not be mislabelled as italic (*...*)."""
        tex = r"\documentclass{article}\begin{document}\textsc{SmallCapsWord}\end{document}"
        doc = LaTeXBuilder().build(tex, arxiv_id="t")
        md = MarkdownEmitter().emit(doc)
        assert "SmallCapsWord" in md
        # Regression A12: previously emitted as *SmallCapsWord* (italic).
        assert "*SmallCapsWord*" not in md

    def test_unknown_pandoc_block_emits_comment_not_json(self):
        """Regression A14: unknown Pandoc block must emit a comment marker.

        Not ``json.dumps(blk)`` which leaked raw JSON into the Markdown.
        """
        builder = LaTeXBuilder()
        raw = builder._block_from_pandoc({"t": "TotallyUnknownBlock"}, "sec", 0)
        assert raw is not None
        assert raw.type == "raw_block"
        assert raw.format == "markdown"
        assert raw.content.startswith("<!--")
        assert "TotallyUnknownBlock" in raw.content
        # No JSON garbage from json.dumps(blk).
        assert "{" not in raw.content
        assert '"' not in raw.content

    def test_split_macro_def_name_joined_for_pandoc(self):
        r"""Regression: Pandoc 3.6.4 aborts when a macro name sits on the next line.

        The name token after a definition keyword (e.g. arXiv 2603.04780) must
        be collapsed onto one line. ``_sanitize_tex_for_pandoc`` does this, and
        ``build()`` must succeed on such input rather than backtracking.
        """
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = "\\DeclareRobustCommand\n  \\cdotsshort{\\cdot}"
        joined = _sanitize_tex_for_pandoc(tex)
        assert joined == "\\DeclareRobustCommand \\cdotsshort{\\cdot}"

        # End-to-end: a document using the split form must build without error.
        doc_tex = r"""\documentclass{article}
\begin{document}
\DeclareRobustCommand
  \foo{FOO}
\foo
\end{document}"""
        doc = LaTeXBuilder().build(doc_tex, arxiv_id="t")
        md = MarkdownEmitter().emit(doc)
        assert "FOO" in md


class TestLaTeXBuilderContentCoverage:
    """Regression: IR LaTeX path must not silently drop abstract or table content.

    Previously LaTeXBuilder put the abstract only in metadata.abstract_text
    (a string the MarkdownEmitter never reads) and failed to extract booktabs
    table cells (pandoc emits Row/Cell/TableBody as bare lists, not {t,c}
    dicts), so both vanished from IR output. Verified via a local legacy-vs-IR
    content-diff on tests/fixtures/sample_paper.tex.
    """

    def test_abstract_emitted_in_ir_markdown(self):
        from pathlib import Path

        from arxiv2md_beta.ir import LaTeXBuilder, MarkdownEmitter

        tex = (Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sample_paper.tex").read_text()
        doc = LaTeXBuilder().build(tex, arxiv_id="s")
        md = MarkdownEmitter().emit(doc)
        assert "## Abstract" in md
        assert "sample abstract for testing" in md

    def test_table_cells_emitted_in_ir_markdown(self):
        from pathlib import Path

        from arxiv2md_beta.ir import LaTeXBuilder, MarkdownEmitter

        tex = (Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sample_paper.tex").read_text()
        doc = LaTeXBuilder().build(tex, arxiv_id="s")
        md = MarkdownEmitter().emit(doc)
        # Header + body cells must appear as a pipe table, not be dropped.
        assert "Method" in md and "Accuracy" in md
        assert "0.95" in md and "0.92" in md
        assert "|" in md  # pipe table rendered
        assert "Comparison of methods" in md  # caption


class TestExtensionProbeRelativePaths:
    """R1.3: extension probing must emit portable relative paths."""

    def test_probe_returns_images_relative_not_absolute(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "teaser.png").write_bytes(b"png")
        tex = (
            r"\documentclass{article}\begin{document}"
            r"\begin{figure}\includegraphics{teaser}\caption{Fig 1: T}\end{figure}"
            r"\end{document}"
        )
        builder = LaTeXBuilder()
        doc = builder.build(tex, arxiv_id="t", base_dir=tmp_path, images_subdir="images")
        srcs = [il.src for sec in doc.sections for blk in sec.blocks if blk.type == "figure" for il in blk.images]
        assert srcs, "figure with probed image not found"
        for src in srcs:
            assert src == "images/teaser.png", f"non-portable src leaked: {src}"
            assert not src.startswith("/")


class TestSanitizeTeXForPandoc:
    """Fixes for constructs Pandoc's LaTeX reader rejects but LaTeX accepts."""

    def test_vskip_glue_stripped(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = r"\newenvironment{proof}[1][. ]{{\bf Proof#1}}{\hfill$\square$\vskip\baselineskip}"
        out = _sanitize_tex_for_pandoc(tex)
        assert r"\vskip" not in out

    def test_hskip_mskip_glue_stripped(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = r"\hskip 1em\mskip\thinmuskip"
        out = _sanitize_tex_for_pandoc(tex)
        assert r"\hskip" not in out and r"\mskip" not in out

    def test_if0_block_stripped_with_else_branch_kept(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = r"before \if0 hidden \else shown \fi after"
        out = _sanitize_tex_for_pandoc(tex)
        assert r"\if0" not in out and r"\fi" not in out
        assert "hidden" not in out
        assert "shown" in out

    def test_if0_nested_blocks_stripped(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = r"\if0 a \ifgamma x \fi b \else c \fi d"
        out = _sanitize_tex_for_pandoc(tex)
        assert "c" in out and "d" in out
        assert "a" not in out and "b" not in out

    def test_iffalse_block_stripped(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = r"a \iffalse gone \fi b"
        out = _sanitize_tex_for_pandoc(tex)
        assert "gone" not in out
        assert "a" in out and "b" in out

    def test_build_succeeds_on_vskip_in_proof_def(self):
        """End-to-end: the exact failing construct from arXiv 1501.01332."""
        tex = r"""\documentclass{article}
\newenvironment{proof}[1][. ]{{\bf Proof#1}}{\hfill$\square$\vskip\baselineskip}
\begin{document}
\begin{proof}
A short proof.
\end{proof}
\end{document}"""
        doc = LaTeXBuilder().build(tex, arxiv_id="t")
        md = MarkdownEmitter().emit(doc)
        assert "A short proof." in md


class TestUnclosedGroupRecovery:
    """A stray unclosed ``{`` group must not abort the whole parse."""

    def test_build_recovers_from_unclosed_brace(self):
        tex = r"\documentclass{article}\begin{document}Hello {world\end{document}"
        doc = LaTeXBuilder().build(tex, arxiv_id="t")
        md = MarkdownEmitter().emit(doc)
        assert "Hello" in md
        assert "world" in md


class TestDisplayMathSplitting:
    """Display math is lifted out of paragraphs into EquationIR blocks."""

    def _emitted(self, tex: str) -> str:
        doc = LaTeXBuilder().build(tex, arxiv_id="t")
        return MarkdownEmitter().emit(doc)

    def test_display_math_not_inline_in_paragraph(self):
        tex = r"\documentclass{article}\begin{document}We have\begin{align}a&=b\end{align}where $x$ is.\end{document}"
        md = self._emitted(tex)
        # display math must be on its own line, not glued to prose
        assert "\n$$\n" in md

    def test_display_math_nested_in_emphasis_is_lifted(self):
        # pandoc wraps theorem-like content in an emphasis container; display
        # math inside it must still become its own $$ block.
        tex = (
            r"\documentclass{article}\begin{document}"
            r"\newtheorem{assumption}{Assumption}"
            r"\begin{assumption}There exists $v$ such that"
            r"\begin{align}a&=b\end{align}"
            r"holds.\end{assumption}"
            r"\end{document}"
        )
        md = self._emitted(tex)
        assert "$$\n" in md
        assert "\n$$\n" in md  # block-isolated, not inline
        assert "There exists" in md
        assert "holds" in md

    def test_label_stripped_from_display_math(self):
        tex = r"\documentclass{article}\begin{document}" r"\begin{align}a&=b\label{eq:x}\end{align}" r"\end{document}"
        md = self._emitted(tex)
        assert r"\label{eq:x}" not in md

    def test_perp_and_mbox_normalized_in_math(self):
        tex = (
            r"\documentclass{article}\begin{document}"
            r"\newcommand{\independent}{\mbox{${}\perp\mkern-11mu\perp{}$}}"
            r"$A \independent B$ and $\operatorname{argmin}_x f(x)$."
            r"\end{document}"
        )
        md = self._emitted(tex)
        assert r"\mbox{${}\perp" not in md
        assert r"\perp \!\!\! \perp" in md
        assert r"\mbox{argmin}" not in md


class TestBblBibliographyResolution:
    r"""``\bibliography{X}`` must inline the arXiv-shipped ``.bbl``."""

    def test_bbl_inlined_and_preamble_stripped(self, tmp_path):
        from arxiv2md_beta.latex.includes import resolve_latex_includes

        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "See \\cite{key1}.\n"
            "\\bibliographystyle{plainnat}\n\\bibliography{bibliography}\n"
            "\\end{document}\n"
        )
        (tmp_path / "main.bbl").write_text(
            "\\begin{thebibliography}{72}\n"
            "\\providecommand{\\natexlab}[1]{#1}\n"
            "\\providecommand{\\url}[1]{\\texttt{#1}}\n"
            "\\expandafter\\ifx\\csname urlstyle\\endcsname\\relax\n"
            "\\providecommand{\\doi}[1]{doi: #1}\\else\n"
            "\\providecommand{\\doi}{doi: \\begingroup \\urlstyle{rm}\\Url}\\fi\n"
            "\n"
            "\\bibitem[Aldrich(1989)]{key1}\nJ.~Aldrich.\n\\newblock Autonomy.\n"
            "\\end{thebibliography}\n"
        )
        resolved = resolve_latex_includes(tmp_path / "main.tex", tmp_path)
        assert r"\begin{thebibliography}" in resolved
        assert r"\bibitem[Aldrich(1989)]{key1}" in resolved
        # Preamble plumbing must be gone, and the {72} width arg with it.
        assert r"\providecommand" not in resolved
        assert "72" not in resolved.replace("thebibliography", "")

    def test_missing_bbl_replaced_with_empty(self, tmp_path):
        from arxiv2md_beta.latex.includes import resolve_latex_includes

        (tmp_path / "main.tex").write_text("\\begin{document}x\\bibliography{nope}\\end{document}\n")
        resolved = resolve_latex_includes(tmp_path / "main.tex", tmp_path)
        assert r"\bibliography" not in resolved

    def test_cites_render_as_numbers_with_bbl(self, tmp_path):
        from arxiv2md_beta.latex.includes import resolve_latex_includes

        tex = (
            "\\documentclass{article}\n\\begin{document}\n"
            "\\section{Intro}\n"
            "See \\cite{key1} and \\citep{key2}.\n"
            "\\bibliography{bibliography}\n"
            "\\end{document}\n"
        )
        (tmp_path / "main.tex").write_text(tex)
        (tmp_path / "main.bbl").write_text(
            "\\begin{thebibliography}{9}\n"
            "\\bibitem[A(1)]{key1}\nA. Author.\n\\newblock Title One.\n"
            "\\bibitem[B(2)]{key2}\nB. Author.\n\\newblock Title Two.\n"
            "\\end{thebibliography}\n"
        )
        resolved = resolve_latex_includes(tmp_path / "main.tex", tmp_path)
        doc = LaTeXBuilder().build(resolved, arxiv_id="t", base_dir=tmp_path)
        md = MarkdownEmitter().emit(doc)
        assert "(1)" in md and "(2)" in md


class TestMathNormalizationRegressions:
    """Math fixes ported from the HTML builder + pandoc-specific artifacts."""

    def _build_md(self, tex: str) -> str:
        doc = LaTeXBuilder().build(tex, arxiv_id="t")
        return MarkdownEmitter().emit(doc)

    def test_perp_replacement_has_trailing_space(self):
        tex = (
            r"\documentclass{article}\begin{document}"
            r"\newcommand{\independent}{\mbox{${}\perp\mkern-11mu\perp{}$}}"
            r"$\varepsilon \independent X_{S}^{e}$"
            r"\end{document}"
        )
        md = self._build_md(tex)
        # The replacement must not glue to the next token (\perpX).
        assert r"\perpX" not in md
        assert r"\perp \!\!\! \perp" in md

    def test_alpha_split_out_of_text(self):
        tex = (
            r"\documentclass{article}\begin{document}" r"$\mbox{ can be rejected at level $\alpha$}$" r"\end{document}"
        )
        md = self._build_md(tex)
        # \alpha must end up in math mode, outside \text{}.
        import re as _re

        m = _re.search(r"\\text\{[^}]*\\alpha", md)
        assert m is None, f"\\alpha still inside \\text: {m.group(0) if m else ''}"

    def test_nolinebreak_stripped(self):
        tex = (
            r"\documentclass{article}\begin{document}"
            r"$P[H_{0,S} \text{ rejected}] \leq \nolinebreak \alpha$"
            r"\end{document}"
        )
        md = self._build_md(tex)
        assert r"\nolinebreak" not in md


class TestBibitemNumbering:
    def test_bibitem_numbers_skip_if0_blocks(self):
        r"""Regression: bibitem numbering ran before ``\if0`` sanitization.

        Commented-out ``\bibitem`` entries used to occupy reference numbers,
        shifting every subsequent ``\cite``.
        """
        tex = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Body text \cite{real}.
\begin{thebibliography}{9}
\if0
\bibitem{ghost} Ghost entry, commented out.
\fi
\bibitem{real} Real Reference. 2020.
\end{thebibliography}
\end{document}"""
        doc = LaTeXBuilder().build(tex, arxiv_id="test")
        md = MarkdownEmitter().emit(doc)
        # Single cite renders as a parenthesised group; the ghost bibitem
        # must not have shifted the number.
        assert "Body text (1)." in md
        assert "(2)" not in md


class TestBibliographyTrailingContent:
    """audit4 PR4.2: content after thebibliography must survive."""

    def test_blocks_after_bibliography_are_kept(self) -> None:
        from arxiv2md_beta.ir import LaTeXBuilder as B

        tex = r"""\documentclass{article}
\begin{document}
\section{Intro}
Body text.
\begin{thebibliography}{9}
\bibitem{a} Author One. Paper A.
\bibitem{b} Author Two. Paper B.
\end{thebibliography}
Acknowledgement text about funding.
\end{document}"""
        doc = B().build(tex, arxiv_id="t")
        titles = [s.title for s in doc.sections]
        assert "References" in titles
        all_text = "\n".join(
            b.inlines[0].text if b.type == "paragraph" and b.inlines else "" for s in doc.sections for b in s.blocks
        )
        assert "Acknowledgement" in all_text, f"post-bibliography content dropped: {titles}"


class TestTableColspanAlignment:
    """audit4 PR4.2: colspan cells repeat so column alignment is preserved."""

    def test_pandoc_colspan_cell_repeats(self) -> None:
        from arxiv2md_beta.ir import LaTeXBuilder as B

        tex = r"""\documentclass{article}
\begin{document}
\begin{tabular}{lll}
\multicolumn{2}{l}{Wide} & Right \\
a & b & c \\
\end{tabular}
\end{document}"""
        doc = B().build(tex, arxiv_id="t")

        def cells_of(sec):
            for blk in sec.blocks:
                if blk.type == "table":
                    return sec, blk
            return None, None

        rows = []

        def collect(secs):
            for s in secs:
                for blk in s.blocks:
                    if blk.type == "table":
                        rows.extend(blk.rows)
                collect(s.children)

        collect(doc.sections)
        assert rows, "table not built"
        assert all(len(r) == 3 for r in rows), f"ragged rows: {[len(r) for r in rows]}"


class TestConditionalCommentAwareness:
    r"""audit5 C4: the \if scanner must not read comments or verbatim text.

    The scanner counted \if…/\fi tokens anywhere in the file. A \if0 opened
    inside a comment (the common "comment out a block with \if0" idiom) with
    its \fi in another comment made the scanner strip every *real* line in
    between; an untoken \fi made it drop the rest of the document entirely
    (``i = n``).
    """

    def test_if0_opened_in_comment_keeps_real_lines(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = "% \\if0 debug block\nreal content line\n% \\fi\nmore content"
        out = _sanitize_tex_for_pandoc(tex)
        assert "real content line" in out
        assert "more content" in out

    def test_commented_pair_around_real_text(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        # % \if0 … % \fi with real text between: nothing may be stripped.
        tex = "keep-a\n% \\if0\nKEEP-B\n% \\fi\nkeep-c"
        out = _sanitize_tex_for_pandoc(tex)
        assert all(k in out for k in ("keep-a", "KEEP-B", "keep-c"))

    def test_if0_inside_verbatim_untouched(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = "\\begin{verbatim}\n\\if0 X \\fi\n\\end{verbatim}\ntail"
        out = _sanitize_tex_for_pandoc(tex)
        assert "tail" in out
        assert "X" in out

    def test_escaped_percent_is_not_a_comment_start(self):
        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = r"win rate 50\% here \if0 x \fi done"
        out = _sanitize_tex_for_pandoc(tex)
        assert "50\\%" in out
        assert "x" not in out
        assert "done" in out

    def test_unterminated_if0_keeps_rest_and_warns(self, caplog):
        import logging

        from arxiv2md_beta.ir.builders.latex import _sanitize_tex_for_pandoc

        tex = "keep me \\if0 never closed\nimportant tail"
        with caplog.at_level(logging.WARNING):
            out = _sanitize_tex_for_pandoc(tex)
        assert "important tail" in out
        assert "nclosed" in caplog.text or "nterminated" in caplog.text


class TestInternalLinkKindClassification:
    """audit5 G1-4: citation kind requires a citation anchor, not a substring.

    'ref' in url.lower() classified #preface and #careful-look ('pre*ref*ace',
    'ca*ref*ul') as citations, and the emitter then dropped the href entirely
    unless linked_citations was on.
    """

    def _link(self, url: str):
        from arxiv2md_beta.ir.builders.latex import LaTeXBuilder

        node = {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "target"}], [url, ""]]}
        return LaTeXBuilder()._inline_from_pandoc(node)

    def test_preface_is_internal_not_citation(self) -> None:
        link = self._link("#preface")
        assert link.kind == "internal"
        assert link.target_id == "preface"

    def test_careful_look_is_internal_not_citation(self) -> None:
        link = self._link("#careful-look")
        assert link.kind == "internal"

    def test_bib_anchor_is_citation(self) -> None:
        link = self._link("#bib.bib3")
        assert link.kind == "citation"

    def test_ref_anchor_is_citation(self) -> None:
        link = self._link("#ref-3")
        assert link.kind == "citation"


class TestTableRowspanAlignment:
    r"""audit5 G1-5: pandoc Cell rowspan must reserve its column in later rows.

    pandoc turns ``\\multirow`` into a Cell with ``rowspan`` (cell_c[2]),
    which the builder used to drop — shifting later columns one left.
    """

    def test_pandoc_rowspan_reserves_column(self) -> None:
        from arxiv2md_beta.ir import LaTeXBuilder as B

        tex = r"""\documentclass{article}
\begin{document}
\begin{tabular}{ll}
\multirow{2}{*}{Span} & top \\
 & bottom \\
\end{tabular}
\end{document}"""
        doc = B().build(tex, arxiv_id="t")

        rows: list[list] = []
        headers = None

        def collect(secs) -> None:
            nonlocal headers
            for s in secs:
                for blk in s.blocks:
                    if blk.type == "table":
                        rows.extend(blk.rows)
                        if headers is None and blk.headers:
                            headers = blk.headers
                collect(s.children)

        collect(doc.sections)
        assert rows, "table not built"
        assert headers is not None and headers[0][0].text == "Span"
        # pandoc assigns no head rows here, so the builder promotes the first
        # grid row to headers; the second row keeps column 2 aligned.
        assert len(rows[0]) == 2, f"row shifted: {[len(r) for r in rows]}"
        assert rows[0][0] == []
        assert rows[0][1][0].text == "bottom"


class TestBibliographySlotPreservation:
    """audit5 G1-6: every thebibliography entry must keep its slot.

    Inline ``[N]`` citations index into the entry list; dropping an
    empty/unparseable entry silently shifted every later citation onto the
    wrong entry. Slots now get placeholders (or keep their printed label).
    """

    @staticmethod
    def _refs_section():
        from arxiv2md_beta.ir import LaTeXBuilder as B

        blocks = [
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "Intro"}]]},
            {"t": "Para", "c": [{"t": "Str", "c": "Body text."}]},
            {
                "t": "Div",
                "c": [
                    ["", ["thebibliography"], []],
                    [
                        {"t": "Para", "c": []},  # entry 1: empty body
                        # entry 2: Div whose children all filter away
                        {
                            "t": "Div",
                            "c": [
                                ["", [], []],
                                [{"t": "Para", "c": []}],
                            ],
                        },
                        # entry 3: numeric-only paragraph (bibitem label)
                        {"t": "Para", "c": [{"t": "Str", "c": "10"}]},
                        # entry 4: a real entry
                        {"t": "Para", "c": [{"t": "Str", "c": "Real entry text."}]},
                    ],
                ],
            },
        ]
        sections = B()._build_sections(blocks)
        refs = [s for s in sections if s.title == "References"]
        assert refs, "References section not materialized"
        return refs[0]

    def test_empty_entries_keep_placeholder_slots(self) -> None:
        items = self._refs_section().blocks[0].items
        assert len(items) == 4, f"entry slots lost: {len(items)}"
        assert items[0][0].inlines[0].text == "[reference entry could not be parsed]"
        assert items[1][0].inlines[0].text == "[reference entry could not be parsed]"

    def test_numeric_label_kept_as_slot_content(self) -> None:
        items = self._refs_section().blocks[0].items
        # the printed label is informative: keep it rather than dropping
        assert items[2][0].inlines[0].text == "10"

    def test_real_entry_unchanged(self) -> None:
        items = self._refs_section().blocks[0].items
        assert items[3][0].inlines[0].text == "Real entry text."


class TestDivAnchorFirstBlockOnly:
    """audit5 G1-7: a Div's anchor must mark its FIRST child block only.

    Spreading the id onto every child emitted one ``<a id="X">`` per block —
    duplicate HTML ids and ambiguous repoint targets.
    """

    def test_anchor_lands_on_first_block_only(self) -> None:
        from arxiv2md_beta.ir import LaTeXBuilder as B

        blk = {
            "t": "Div",
            "c": [
                ["fig-wrap", [], []],
                [
                    {"t": "Para", "c": [{"t": "Str", "c": "one"}]},
                    {"t": "Para", "c": [{"t": "Str", "c": "two"}]},
                    {"t": "Para", "c": [{"t": "Str", "c": "three"}]},
                ],
            ],
        }
        out = B()._block_from_pandoc(blk)
        assert isinstance(out, list) and len(out) == 3
        assert [b.anchor for b in out] == ["fig-wrap", None, None]

    def test_child_own_anchor_not_overwritten(self) -> None:
        from arxiv2md_beta.ir import LaTeXBuilder as B

        blk = {
            "t": "Div",
            "c": [
                ["outer", [], []],
                [
                    {"t": "Header", "c": [2, ["inner", [], []], [{"t": "Str", "c": "Sub"}]]},
                    {"t": "Para", "c": [{"t": "Str", "c": "text"}]},
                ],
            ],
        }
        out = B()._block_from_pandoc(blk)
        assert isinstance(out, list) and len(out) == 2
        assert out[0].anchor == "inner"
        assert out[1].anchor == "outer"
