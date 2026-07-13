"""Tests for AnchorPass."""

from __future__ import annotations

from arxiv2md_beta.ir import (
    DocumentIR,
    EquationIR,
    FigureIR,
    ImageRefIR,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
)
from arxiv2md_beta.ir.transforms.anchor import AnchorPass


def test_anchor_pass_processes_abstract_and_front_matter():
    """Regression: AnchorPass must process abstract and front_matter.

    Previously it iterated only doc.sections, so abstract figures/equations got
    no anchor while NumberingPass (which does process abstract) had already
    assigned figure_id/equation_number to them.
    """
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        abstract=[
            FigureIR(figure_id="figure-1", images=[ImageRefIR(src="./f1.png")]),
            EquationIR(equation_number="1", latex="x=1"),
        ],
        front_matter=[
            FigureIR(figure_id="figure-2", images=[ImageRefIR(src="./f2.png")]),
        ],
        sections=[
            SectionIR(
                title="Body",
                level=1,
                blocks=[ParagraphIR(inlines=[])],
            ),
        ],
    )

    AnchorPass().run(doc)

    assert doc.abstract[0].anchor == "figure-1"
    assert doc.abstract[1].anchor == "eq-1"
    assert doc.front_matter[0].anchor == "figure-2"


def test_anchor_pass_does_not_overwrite_existing_anchor():
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        abstract=[FigureIR(figure_id="figure-9", anchor="custom-anchor", images=[ImageRefIR(src="./f.png")])],
        sections=[],
    )
    AnchorPass().run(doc)
    assert doc.abstract[0].anchor == "custom-anchor"
