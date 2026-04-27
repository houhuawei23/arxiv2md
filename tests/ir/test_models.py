"""Tests for IR model construction, serialization, and discriminated unions."""

from __future__ import annotations

import json
import pytest

from arxiv2md_beta.ir import (
    AlgorithmIR,
    BlockQuoteIR,
    BreakIR,
    CodeIR,
    DocumentIR,
    EmphasisIR,
    EquationIR,
    FigureIR,
    HeadingIR,
    ImageRefIR,
    LinkIR,
    ListIR,
    MathIR,
    PaperMetadata,
    ParagraphIR,
    RawBlockIR,
    RawInlineIR,
    RuleIR,
    SectionIR,
    SubscriptIR,
    SuperscriptIR,
    TableIR,
    TextIR,
    walk,
    NodeCounter,
)


# ── Basic construction ────────────────────────────────────────────────


class TestInlineConstruction:
    def test_text_ir(self):
        n = TextIR(text="hello")
        assert n.type == "text"
        assert n.text == "hello"

    def test_emphasis_ir(self):
        n = EmphasisIR(style="bold", inlines=[TextIR(text="bold text")])
        assert n.type == "emphasis"
        assert n.style == "bold"
        assert len(n.inlines) == 1
        assert n.inlines[0].type == "text"

    def test_link_ir(self):
        n = LinkIR(kind="external", url="https://example.com", inlines=[TextIR(text="click")])
        assert n.type == "link"
        assert n.kind == "external"
        assert n.url == "https://example.com"

    def test_math_ir_inline(self):
        n = MathIR(latex="E=mc^2", display=False)
        assert n.type == "math"
        assert n.display is False

    def test_math_ir_display(self):
        n = MathIR(latex="E=mc^2", display=True)
        assert n.display is True

    def test_raw_inline_ir(self):
        n = RawInlineIR(format="html", content="<span>raw</span>")
        assert n.type == "raw_inline"
        assert n.format == "html"
        assert n.content == "<span>raw</span>"

    def test_nested_emphasis(self):
        """Bold text containing italic text."""
        n = EmphasisIR(
            style="bold",
            inlines=[
                TextIR(text="bold"),
                EmphasisIR(style="italic", inlines=[TextIR(text="italic")]),
            ],
        )
        assert n.inlines[0].type == "text"
        assert n.inlines[1].type == "emphasis"
        assert n.inlines[1].style == "italic"


class TestBlockConstruction:
    def test_paragraph_ir(self):
        b = ParagraphIR(inlines=[TextIR(text="hello world")])
        assert b.type == "paragraph"
        assert len(b.inlines) == 1

    def test_heading_ir(self):
        b = HeadingIR(level=3, inlines=[TextIR(text="A subsection")])
        assert b.type == "heading"
        assert b.level == 3

    def test_figure_ir(self):
        b = FigureIR(
            figure_id="figure-1",
            images=[ImageRefIR(src="./images/fig1.png", alt="Figure 1")],
            caption=[TextIR(text="Figure 1: Overview")],
        )
        assert b.type == "figure"
        assert b.figure_id == "figure-1"
        assert len(b.images) == 1

    def test_table_ir(self):
        b = TableIR(
            table_id="table-1",
            headers=[[TextIR(text="A")], [TextIR(text="B")]],
            rows=[
                [[TextIR(text="1")], [TextIR(text="2")]],
            ],
        )
        assert b.type == "table"
        assert len(b.headers) == 2
        assert len(b.rows) == 1

    def test_equation_ir(self):
        b = EquationIR(latex="x^2 + y^2 = 1", equation_number="(1)")
        assert b.type == "equation"
        assert b.latex == "x^2 + y^2 = 1"

    def test_list_ir_ordered(self):
        b = ListIR(ordered=True, items=[
            [ParagraphIR(inlines=[TextIR(text="item 1")])],
            [ParagraphIR(inlines=[TextIR(text="item 2")])],
        ])
        assert b.type == "list"
        assert b.ordered is True
        assert len(b.items) == 2

    def test_code_ir(self):
        b = CodeIR(language="python", text="x = 1")
        assert b.type == "code"
        assert b.language == "python"

    def test_blockquote_ir(self):
        b = BlockQuoteIR(blocks=[
            ParagraphIR(inlines=[TextIR(text="quoted")])
        ])
        assert b.type == "blockquote"
        assert len(b.blocks) == 1

    def test_rule_ir(self):
        b = RuleIR()
        assert b.type == "rule"

    def test_raw_block_ir(self):
        b = RawBlockIR(format="html", content="<div>raw</div>")
        assert b.type == "raw_block"
        assert b.content == "<div>raw</div>"


# ── Structural identifiers on the base class ──────────────────────────


class TestStructuralIdentifiers:
    def test_block_has_base_fields(self):
        b = ParagraphIR(
            id="sec_0:b0:paragraph",
            section_id="sec_0",
            order_index=0,
            label="lbl:test",
            inlines=[TextIR(text="text")],
        )
        assert b.id == "sec_0:b0:paragraph"
        assert b.section_id == "sec_0"
        assert b.order_index == 0
        assert b.label == "lbl:test"

    def test_source_loc(self):
        from arxiv2md_beta.ir import SourceLoc
        loc = SourceLoc(file="test.html", line_start=42, parser="html")
        assert loc.file == "test.html"
        assert loc.parser == "html"


# ── Discriminated union serialization ──────────────────────────────────


class TestDiscriminatedUnion:
    def test_inline_union_roundtrip(self):
        """InlineUnion JSON roundtrip preserves type and data."""
        from arxiv2md_beta.ir.inlines import InlineUnion
        from typing import get_args

        # Build a snippet via ParagraphIR to exercise Union
        p = ParagraphIR(inlines=[
            TextIR(text="Hello "),
            EmphasisIR(style="bold", inlines=[TextIR(text="world")]),
        ])
        js = p.model_dump_json()
        data = json.loads(js)
        assert data["inlines"][0]["type"] == "text"
        assert data["inlines"][1]["type"] == "emphasis"
        # Re-parse
        p2 = ParagraphIR.model_validate_json(js)
        assert p2.inlines[0].type == "text"
        assert p2.inlines[1].type == "emphasis"
        assert p2.inlines[1].style == "bold"

    def test_block_union_roundtrip(self):
        """BlockUnion roundtrip preserves all block types."""
        section = SectionIR(
            title="Test",
            level=1,
            blocks=[
                ParagraphIR(inlines=[TextIR(text="p")]),
                EquationIR(latex="x=1"),
                RuleIR(),
            ],
        )
        js = section.model_dump_json()
        s2 = SectionIR.model_validate_json(js)
        assert len(s2.blocks) == 3
        assert s2.blocks[0].type == "paragraph"
        assert s2.blocks[1].type == "equation"
        assert s2.blocks[2].type == "rule"

    def test_every_inline_type_serializes(self):
        """Smoke-test that every inline type serializes without error."""
        inlines = [
            TextIR(text="text"),
            EmphasisIR(style="italic", inlines=[TextIR(text="i")]),
            LinkIR(kind="external", url="http://a", inlines=[TextIR(text="l")]),
            LinkIR(kind="internal", target_id="s1", inlines=[TextIR(text="l")]),
            LinkIR(kind="citation", target_id="ref-1", inlines=[TextIR(text="[1]")]),
            MathIR(latex="x", display=False),
            MathIR(latex="y", display=True),
            ImageRefIR(src="./a.png", alt="a"),
            SuperscriptIR(inlines=[TextIR(text="sup")]),
            SubscriptIR(inlines=[TextIR(text="sub")]),
            BreakIR(),
            RawInlineIR(format="html", content="<b>x</b>"),
        ]
        p = ParagraphIR(inlines=inlines)
        js = p.model_dump_json()
        p2 = ParagraphIR.model_validate_json(js)
        assert len(p2.inlines) == len(inlines)

    def test_every_block_type_serializes(self):
        """Smoke-test that every block type serializes without error."""
        blocks = [
            ParagraphIR(inlines=[TextIR(text="p")]),
            HeadingIR(level=2, inlines=[TextIR(text="h")]),
            FigureIR(images=[ImageRefIR(src="./a.png")], caption=[TextIR(text="c")]),
            TableIR(headers=[[TextIR(text="h")]], rows=[]),
            ListIR(items=[[ParagraphIR(inlines=[TextIR(text="i")])]]),
            CodeIR(text="print(1)"),
            EquationIR(latex="x=1"),
            BlockQuoteIR(blocks=[ParagraphIR(inlines=[TextIR(text="q")])]),
            AlgorithmIR(caption=[TextIR(text="A")]),
            RuleIR(),
            RawBlockIR(format="html", content="<div>x</div>"),
        ]
        section = SectionIR(title="All", level=1, blocks=blocks)
        js = section.model_dump_json()
        s2 = SectionIR.model_validate_json(js)
        assert len(s2.blocks) == len(blocks)


# ── DocumentIR roundtrip ───────────────────────────────────────────────


class TestDocumentIRRoundtrip:
    def test_full_roundtrip(self, complex_doc):
        """A complex DocumentIR survives JSON roundtrip intact."""
        js = complex_doc.model_dump_json(indent=2)
        doc2 = DocumentIR.model_validate_json(js)

        assert doc2.schema_version == "2.0"
        assert doc2.metadata.arxiv_id == "2501.12345"
        assert doc2.metadata.title == "A Comprehensive Study of IR Systems"
        assert doc2.metadata.authors == ["Alice Foo", "Bob Bar"]
        assert len(doc2.sections) == 2
        assert doc2.sections[0].struct_id == "sec_0"
        assert doc2.sections[0].children[0].struct_id == "sec_0_0"

        # Verify deep structure survived
        sec1 = doc2.sections[0]
        assert sec1.blocks[0].type == "paragraph"
        assert sec1.blocks[3].type == "figure"
        assert sec1.blocks[3].figure_id == "figure-1"

    def test_minimal_roundtrip(self, minimal_doc):
        js = minimal_doc.model_dump_json()
        doc2 = DocumentIR.model_validate_json(js)
        assert doc2.metadata.arxiv_id == "2501.12345"
        assert doc2.sections[0].title == "Introduction"


# ── Node counting via IRWalker ─────────────────────────────────────────


class TestNodeCounting:
    def test_count_inlines(self, complex_doc):
        """IRWalker should traverse deeply nested inlines."""
        counter = NodeCounter()
        walk(complex_doc, counter)
        counts = counter.counts
        # Should have at least: text, emphasis, link, math, paragraph, heading,
        # figure, code, table, equation, blockquote, list, rule, section, document
        assert counts.get("text", 0) > 5
        assert counts.get("emphasis", 0) >= 2
        assert counts.get("link", 0) >= 2
        assert counts.get("paragraph", 0) >= 5
        assert counts.get("figure", 0) == 1
        assert counts.get("table", 0) == 1
        assert counts.get("equation", 0) == 1
        assert counts.get("list", 0) >= 2  # 1 top-level + 1 sub-list
        assert counts.get("code", 0) == 1
        assert counts.get("rule", 0) == 1
        assert counts.get("section", 0) == 3  # 2 top-level + 1 child
        assert counts.get("document", 0) == 1

    def test_count_empty_doc(self, minimal_doc):
        counter = NodeCounter()
        walk(minimal_doc, counter)
        counts = counter.counts
        assert counts.get("document", 0) == 1
        assert counts.get("section", 0) == 1
        assert counts.get("paragraph", 0) == 2  # 1 abstract + 1 section
        assert counts.get("text", 0) == 2     # 1 abstract + 1 section


# ── Field validation ───────────────────────────────────────────────────


class TestFieldValidation:
    def test_heading_level_bounds(self):
        """level must be 1-6."""
        with pytest.raises(ValueError):
            HeadingIR(level=0, inlines=[TextIR(text="bad")])
        with pytest.raises(ValueError):
            HeadingIR(level=7, inlines=[TextIR(text="bad")])
        # Valid boundaries
        HeadingIR(level=1, inlines=[TextIR(text="ok")])
        HeadingIR(level=6, inlines=[TextIR(text="ok")])

    def test_section_level_bounds(self):
        with pytest.raises(ValueError):
            SectionIR(title="Bad", level=0, blocks=[])
        with pytest.raises(ValueError):
            SectionIR(title="Bad", level=7, blocks=[])

    def test_extra_fields_forbidden(self):
        """Extra fields should be rejected."""
        with pytest.raises(ValueError):
            TextIR(text="hello", extra_field="nope")
