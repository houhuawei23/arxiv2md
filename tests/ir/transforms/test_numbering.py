"""Tests for NumberingPass."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    EquationIR,
    FigureIR,
    ImageRefIR,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
    TableIR,
    TextIR,
)
from arxiv2md_beta.ir.transforms.numbering import NumberingPass


@pytest.fixture
def doc() -> DocumentIR:
    return DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(
                title="S1",
                level=1,
                blocks=[
                    FigureIR(images=[ImageRefIR(src="./a.png")]),
                    FigureIR(images=[ImageRefIR(src="./b.png")]),
                    TableIR(headers=[[TextIR(text="A")]], rows=[]),
                    EquationIR(latex="x=1"),
                    EquationIR(latex="y=2"),
                ],
            ),
        ],
    )


class TestNumberingPass:
    def test_figures_numbered(self, doc):
        NumberingPass().run(doc)
        figs = [b for b in doc.sections[0].blocks if b.type == "figure"]
        assert figs[0].figure_id == "figure-1"
        assert figs[1].figure_id == "figure-2"

    def test_tables_numbered(self, doc):
        NumberingPass().run(doc)
        tbls = [b for b in doc.sections[0].blocks if b.type == "table"]
        assert tbls[0].table_id == "table-1"

    def test_equations_numbered(self, doc):
        NumberingPass().run(doc)
        eqs = [b for b in doc.sections[0].blocks if b.type == "equation"]
        assert eqs[0].equation_number == "(1)"
        assert eqs[1].equation_number == "(2)"

    def test_stable_ids(self, doc):
        """Existing figure_id is preserved."""
        doc.sections[0].blocks.insert(0, FigureIR(
            figure_id="existing-id",
            images=[ImageRefIR(src="./x.png")],
        ))
        NumberingPass().run(doc)
        figs = [b for b in doc.sections[0].blocks if b.type == "figure"]
        assert figs[0].figure_id == "existing-id"
        assert figs[1].figure_id == "figure-1"
        assert figs[2].figure_id == "figure-2"

    def test_empty_doc(self):
        doc = DocumentIR(metadata=PaperMetadata(arxiv_id="test"))
        NumberingPass().run(doc)
        # Should not raise

    def test_nested_sections(self):
        """Numbering works through nested sections."""
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Parent", level=1,
                    blocks=[FigureIR(images=[ImageRefIR(src="./a.png")])],
                    children=[
                        SectionIR(
                            title="Child", level=2,
                            blocks=[FigureIR(images=[ImageRefIR(src="./b.png")])],
                        ),
                    ],
                ),
                SectionIR(
                    title="S2", level=1,
                    blocks=[FigureIR(images=[ImageRefIR(src="./c.png")])],
                ),
            ],
        )
        NumberingPass().run(doc)

        all_figs = []
        def collect(s):
            for b in s.blocks:
                if b.type == "figure":
                    all_figs.append(b.figure_id)
            for c in s.children:
                collect(c)
        for s in doc.sections:
            collect(s)

        assert all_figs == ["figure-1", "figure-2", "figure-3"]


class TestPassPipeline:
    def test_pipeline(self):
        from arxiv2md_beta.ir.transforms.base import PassPipeline
        from arxiv2md_beta.ir.transforms.anchor import AnchorPass

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Intro", level=1,
                    blocks=[FigureIR(images=[ImageRefIR(src="./a.png")])],
                ),
            ],
        )

        pp = PassPipeline()
        pp.add(NumberingPass())
        pp.add(AnchorPass())
        doc = pp.run(doc)

        fig = doc.sections[0].blocks[0]
        assert fig.figure_id == "figure-1"
        assert fig.anchor == "figure-1"
