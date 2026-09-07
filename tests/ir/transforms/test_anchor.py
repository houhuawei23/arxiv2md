"""Tests for AnchorPass."""

from __future__ import annotations

from arxiv2md_beta.ir import (
    DocumentIR,
    EquationIR,
    FigureIR,
    ImageRefIR,
    LinkIR,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
    TextIR,
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


def test_anchor_pass_deduplicates_same_titled_sections():
    """Regression: two sections with the same title must not share a slug."""
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(title="Discussion", level=1, blocks=[ParagraphIR(inlines=[])]),
            SectionIR(title="Discussion", level=1, blocks=[ParagraphIR(inlines=[])]),
            SectionIR(title="Discussion!", level=1, blocks=[ParagraphIR(inlines=[])]),
        ],
    )
    AnchorPass().run(doc)
    anchors = [s.anchor for s in doc.sections]
    assert len(set(anchors)) == 3
    assert anchors[0] == "discussion"
    assert anchors[1] == "discussion-2"
    assert anchors[2] == "discussion-3"


def test_anchor_pass_prescan_sees_block_anchors():
    """Auto section slugs must not collide with anchors already on blocks."""
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(
                title="Results",
                level=1,
                blocks=[FigureIR(figure_id="results", images=[ImageRefIR(src="./f.png")])],
            ),
            SectionIR(title="Results", level=1, blocks=[ParagraphIR(inlines=[])]),
        ],
    )
    AnchorPass().run(doc)
    anchors = [doc.sections[0].anchor, doc.sections[1].anchor, doc.sections[0].blocks[0].anchor]
    assert len(set(anchors)) == 3


def test_anchor_pass_unnumbered_equation_gets_ordinal_id():
    """Unnumbered equations get a valid eq-<n> id, not the invalid eq-?."""
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(
                title="Body",
                level=1,
                blocks=[EquationIR(latex="y=2"), EquationIR(latex="z=3")],
            ),
        ],
    )
    AnchorPass().run(doc)
    anchors = [b.anchor for b in doc.sections[0].blocks]
    assert anchors[0] == "eq-1"
    assert anchors[1] == "eq-2"
    assert all(a and "?" not in a for a in anchors)


def test_anchor_pass_unnumbered_equation_skips_claimed_numbers():
    """The ordinal fallback must skip numbers already used by numbered equations."""
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(
                title="Body",
                level=1,
                blocks=[EquationIR(equation_number="1", latex="x=1"), EquationIR(latex="y=2")],
            ),
        ],
    )
    AnchorPass().run(doc)
    anchors = [b.anchor for b in doc.sections[0].blocks]
    assert anchors[0] == "eq-1"
    assert anchors[1] == "eq-2"


def test_anchor_pass_repoints_section_fragments_to_real_anchors():
    """Repoint raw arXiv section fragments to actual slug anchors.

    S2 / S2.SS1 no longer stay as dead positional guesses (section-2 etc.).
    """
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(title="Intro", level=1, blocks=[ParagraphIR(inlines=[])]),
            SectionIR(
                title="Methods & Data",
                level=1,
                blocks=[
                    ParagraphIR(
                        inlines=[
                            TextIR(text="see "),
                            LinkIR(kind="internal", target_id="S2", inlines=[TextIR(text="Methods")]),
                            TextIR(text=" and "),
                            LinkIR(kind="internal", target_id="S2.SS1", inlines=[TextIR(text="Data")]),
                        ]
                    )
                ],
                children=[SectionIR(title="Data", level=2, blocks=[ParagraphIR(inlines=[])])],
            ),
        ],
    )
    AnchorPass().run(doc)
    para = doc.sections[1].blocks[0]
    targets = [il.target_id for il in para.inlines if getattr(il, "type", "") == "link"]
    assert targets == ["methods-data", "data"]


def test_anchor_pass_leaves_non_section_fragments_alone():
    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(
                title="Body",
                level=1,
                blocks=[
                    ParagraphIR(
                        inlines=[
                            LinkIR(kind="internal", target_id="figure-1", inlines=[TextIR(text="fig")]),
                            LinkIR(kind="internal", target_id="S1.F1", inlines=[TextIR(text="raw")]),
                        ]
                    )
                ],
            ),
        ],
    )
    AnchorPass().run(doc)
    targets = [il.target_id for il in doc.sections[0].blocks[0].inlines if getattr(il, "type", "") == "link"]
    assert targets == ["figure-1", "S1.F1"]  # S1.F1 maps to a figure, not a section
