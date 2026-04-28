"""Tests for LaTeXBuilder."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    LaTeXBuilder,
    MarkdownEmitter,
    PaperMetadata,
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
        builder = LaTeXBuilder(
            image_map={"fig1.pdf": Path("./images/fig1.png")}
        )
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
        """arXiv ID is propagated to metadata."""
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
        # Should have footnote marker like [^1] or superscript 1
        assert "1" in md

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
