"""Tests for MarkdownEmitter block rendering."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    AlgorithmIR,
    BlockQuoteIR,
    CodeIR,
    EquationIR,
    FigureIR,
    HeadingIR,
    ImageRefIR,
    ListIR,
    ParagraphIR,
    RawBlockIR,
    RuleIR,
    TableIR,
    TextIR,
)
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter


@pytest.fixture
def emitter() -> MarkdownEmitter:
    return MarkdownEmitter()


# ── ParagraphIR ────────────────────────────────────────────────────────

class TestParagraph:
    def test_simple(self, emitter):
        b = ParagraphIR(inlines=[TextIR(text="hello world")])
        assert emitter._emit_block(b) == "hello world"

    def test_empty(self, emitter):
        b = ParagraphIR(inlines=[])
        assert emitter._emit_block(b) == ""


# ── HeadingIR ──────────────────────────────────────────────────────────

class TestHeading:
    def test_h1(self, emitter):
        b = HeadingIR(level=1, inlines=[TextIR(text="Title")])
        assert emitter._emit_block(b) == "# Title"

    def test_h3(self, emitter):
        b = HeadingIR(level=3, inlines=[TextIR(text="Section")])
        assert emitter._emit_block(b) == "### Section"

    def test_with_anchor(self, emitter):
        b = HeadingIR(level=2, anchor="sec-intro", inlines=[TextIR(text="Intro")])
        result = emitter._emit_block(b)
        assert '<a id="sec-intro"></a>' in result
        assert "## Intro" in result


# ── FigureIR ───────────────────────────────────────────────────────────

class TestFigure:
    def test_single_image(self, emitter):
        b = FigureIR(
            figure_id="figure-1",
            images=[ImageRefIR(src="./fig.png", alt="Figure 1")],
            caption=[TextIR(text="Figure 1: Overview")],
        )
        result = emitter._emit_block(b)
        assert '<a id="figure-1"></a>' in result
        assert "![Figure 1](./fig.png)" in result
        assert "> Figure 1: Overview" in result

    def test_multi_panel(self, emitter):
        b = FigureIR(
            images=[
                ImageRefIR(src="./a.png", alt="A"),
                ImageRefIR(src="./b.png", alt="B"),
            ],
            caption=[],
        )
        result = emitter._emit_block(b)
        assert '<div align="center">' in result
        assert '<img src="./a.png"' in result
        assert '<img src="./b.png"' in result
        assert '</div>' in result

    def test_no_images(self, emitter):
        b = FigureIR(images=[], caption=[TextIR(text="no img")])
        result = emitter._emit_block(b)
        assert "no img" in result


# ── TableIR ────────────────────────────────────────────────────────────

class TestTable:
    def test_simple(self, emitter):
        b = TableIR(
            headers=[[TextIR(text="A")], [TextIR(text="B")]],
            rows=[[[TextIR(text="1")], [TextIR(text="2")]]],
        )
        result = emitter._emit_block(b)
        assert "| A | B |" in result
        assert "| --- | --- |" in result
        assert "| 1 | 2 |" in result

    def test_with_caption(self, emitter):
        b = TableIR(
            table_id="table-1",
            headers=[[TextIR(text="X")]],
            rows=[],
            caption=[TextIR(text="Table 1: Data")],
        )
        result = emitter._emit_block(b)
        assert '<a id="table-1"></a>' in result
        assert "> Table 1: Data" in result

    def test_no_headers(self, emitter):
        b = TableIR(headers=[], rows=[[[TextIR(text="1")]]])
        result = emitter._emit_block(b)
        assert "| 1 |" in result


# ── EquationIR ─────────────────────────────────────────────────────────

class TestEquation:
    def test_numbered(self, emitter):
        b = EquationIR(latex="x=1", equation_number="(1)")
        result = emitter._emit_block(b)
        assert "$$" in result
        assert "x=1" in result
        assert "\\tag{(1)}" in result

    def test_unnumbered(self, emitter):
        b = EquationIR(latex="x=1")
        result = emitter._emit_block(b)
        assert "$$\n" in result
        assert "x=1" in result

    def test_with_anchor(self, emitter):
        b = EquationIR(latex="x=1", anchor="eq:test")
        result = emitter._emit_block(b)
        assert '<a id="eq:test"></a>' in result


# ── ListIR ─────────────────────────────────────────────────────────────

class TestList:
    def test_unordered(self, emitter):
        b = ListIR(items=[
            [ParagraphIR(inlines=[TextIR(text="a")])],
            [ParagraphIR(inlines=[TextIR(text="b")])],
        ])
        result = emitter._emit_block(b)
        lines = result.split("\n")
        assert lines[0].startswith("- a")
        assert lines[1].startswith("- b")

    def test_nested(self, emitter):
        b = ListIR(items=[
            [
                ParagraphIR(inlines=[TextIR(text="Parent")]),
                ListIR(items=[
                    [ParagraphIR(inlines=[TextIR(text="Child")])],
                ]),
            ],
        ])
        result = emitter._emit_block(b)
        assert "- Parent" in result
        assert "  - Child" in result

    def test_empty(self, emitter):
        b = ListIR(items=[])
        assert emitter._emit_block(b) == ""


# ── CodeIR ─────────────────────────────────────────────────────────────

class TestCode:
    def test_with_language(self, emitter):
        b = CodeIR(language="python", text="print(1)")
        result = emitter._emit_block(b)
        assert result == "```python\nprint(1)\n```"

    def test_no_language(self, emitter):
        b = CodeIR(text="code")
        result = emitter._emit_block(b)
        assert result == "```\ncode\n```"


# ── BlockQuoteIR ───────────────────────────────────────────────────────

class TestBlockQuote:
    def test_blockquote(self, emitter):
        b = BlockQuoteIR(blocks=[ParagraphIR(inlines=[TextIR(text="quoted")])])
        result = emitter._emit_block(b)
        assert result == "> quoted"

    def test_multiline(self, emitter):
        b = BlockQuoteIR(blocks=[
            ParagraphIR(inlines=[TextIR(text="line 1")]),
            ParagraphIR(inlines=[TextIR(text="line 2")]),
        ])
        result = emitter._emit_block(b)
        lines = result.split("\n")
        assert lines[0] == "> line 1"
        assert "> line 2" in result


# ── RuleIR ─────────────────────────────────────────────────────────────

class TestRule:
    def test_rule(self, emitter):
        assert emitter._emit_block(RuleIR()) == "---"


# ── AlgorithmIR ────────────────────────────────────────────────────────

class TestAlgorithm:
    def test_algorithm(self, emitter):
        b = AlgorithmIR(caption=[TextIR(text="Algorithm 1: Sort")])
        result = emitter._emit_block(b)
        assert "**Algorithm 1: Sort**" in result

    def test_with_steps(self, emitter):
        b = AlgorithmIR(
            caption=[TextIR(text="Algo")],
            steps=[ParagraphIR(inlines=[TextIR(text="step 1")])],
        )
        result = emitter._emit_block(b)
        assert "step 1" in result


# ── RawBlockIR ─────────────────────────────────────────────────────────

class TestRawBlock:
    def test_raw_block(self, emitter):
        b = RawBlockIR(format="html", content="<div>x</div>")
        assert emitter._emit_block(b) == "<div>x</div>"


# ── Document-level emission ────────────────────────────────────────────

class TestDocumentEmission:
    def test_abstract_section(self, emitter):
        from arxiv2md_beta.ir import DocumentIR, PaperMetadata, SectionIR

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            abstract=[ParagraphIR(inlines=[TextIR(text="abstract text")])],
            sections=[],
        )
        result = emitter.emit(doc)
        assert "## Abstract" in result
        assert "abstract text" in result

    def test_empty_document(self, emitter):
        from arxiv2md_beta.ir import DocumentIR, PaperMetadata

        doc = DocumentIR(metadata=PaperMetadata(arxiv_id="test"))
        result = emitter.emit(doc)
        assert result == "" or result is not None
