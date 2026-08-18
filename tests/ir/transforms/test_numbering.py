"""Tests for NumberingPass."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    EquationIR,
    FigureIR,
    ImageRefIR,
    LinkIR,
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
        doc.sections[0].blocks.insert(
            0,
            FigureIR(
                figure_id="existing-id",
                images=[ImageRefIR(src="./x.png")],
            ),
        )
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
                    title="Parent",
                    level=1,
                    blocks=[FigureIR(images=[ImageRefIR(src="./a.png")])],
                    children=[
                        SectionIR(
                            title="Child",
                            level=2,
                            blocks=[FigureIR(images=[ImageRefIR(src="./b.png")])],
                        ),
                    ],
                ),
                SectionIR(
                    title="S2",
                    level=1,
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
        from arxiv2md_beta.ir.transforms.anchor import AnchorPass
        from arxiv2md_beta.ir.transforms.base import PassPipeline

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Intro",
                    level=1,
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


class TestNumberingUniqueness:
    """R2.1: NumberingPass is the single numbering source with unique anchors."""

    def test_duplicate_caption_ids_get_unique_anchors(self) -> None:
        # Body Figure 1 + appendix figure whose caption repeats "Figure 1":
        # both keep figure_id "figure-1" (caption semantics), anchors differ.
        f1 = FigureIR(figure_id="figure-1", anchor="figure-1", images=[])
        f2 = FigureIR(figure_id="figure-1", anchor="figure-1", images=[])
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="html"),
            sections=[SectionIR(title="S", level=1, blocks=[f1, f2])],
        )
        NumberingPass().run(doc)
        assert f1.figure_id == "figure-1" and f2.figure_id == "figure-1"
        assert f1.anchor == "figure-1"
        assert f2.anchor == "figure-1-2"

    def test_auto_ids_skip_caption_claimed_numbers(self) -> None:
        # A caption-derived "figure-5" must not be re-issued to an uncaptioned figure.
        f1 = FigureIR(figure_id="figure-5", anchor="figure-5", images=[])
        f2 = FigureIR(images=[])
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="html"),
            sections=[SectionIR(title="S", level=1, blocks=[f1, f2])],
        )
        NumberingPass().run(doc)
        assert f2.figure_id == "figure-1"

    def test_equation_number_drives_own_anchor(self) -> None:
        # Appendix eq "(1)" after body eq 1: anchor follows the equation's own
        # number and is deduplicated, not the positional counter.
        e1 = EquationIR(latex="a=b", equation_number="(1)")
        e2 = EquationIR(latex="c=d", equation_number="(1)")
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="html"),
            sections=[SectionIR(title="S", level=1, blocks=[e1, e2])],
        )
        NumberingPass().run(doc)
        assert e1.anchor == "eq-1"
        assert e2.anchor == "eq-1-2"
        assert e2.equation_number == "(1)"

    def test_all_anchors_unique_random_tree(self) -> None:
        """Property-ish check: mixed caption/auto numbering yields unique anchors."""
        import random

        random.seed(7)
        blocks = []
        for i in range(20):
            if i % 3 == 0:
                n = random.randint(1, 4)  # caption numbers repeat heavily
                blocks.append(FigureIR(figure_id=f"figure-{n}", anchor=f"figure-{n}", images=[]))
            else:
                blocks.append(FigureIR(images=[]))
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="html"), sections=[SectionIR(title="S", level=1, blocks=blocks)]
        )
        NumberingPass().run(doc)
        anchors = [b.anchor for b in blocks]
        assert len(anchors) == len(set(anchors)), "duplicate anchors produced"
        # Auto-assigned ids (uncaptioned, i % 3 != 0) are unique and never
        # collide with caption-derived ids.
        caption_ids = {blocks[i].figure_id for i in range(20) if i % 3 == 0}
        auto_ids = [blocks[i].figure_id for i in range(20) if i % 3 != 0]
        assert len(auto_ids) == len(set(auto_ids))
        assert not (set(auto_ids) & caption_ids)


class TestFragmentLinkRepointing:
    """R2.3: internal links to arXiv fragments resolve to real anchors."""

    def test_link_to_labeled_figure_resolves(self) -> None:
        # Figure carries the arXiv element id as label; an internal link that
        # kept the raw fragment is re-pointed to the final anchor.
        fig = FigureIR(label="S1.F1", images=[])
        para = ParagraphIR(inlines=[LinkIR(kind="internal", target_id="S1.F1", inlines=[TextIR(text="see figure")])])
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="html"),
            sections=[SectionIR(title="S", level=1, blocks=[para, fig])],
        )
        NumberingPass().run(doc)
        assert fig.figure_id == "figure-1" and fig.anchor == "figure-1"
        assert para.inlines[0].target_id == "figure-1"

    def test_appendix_label_numbering_stays_local(self) -> None:
        # Supplementary id "A1.F1" with caption "Figure 1": link resolves to
        # the caption anchor, not a guessed global counter.
        fig = FigureIR(label="A1.F1", figure_id="figure-1", anchor="figure-1", images=[])
        para = ParagraphIR(inlines=[LinkIR(kind="internal", target_id="A1.F1", inlines=[TextIR(text="x")])])
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="html"),
            sections=[SectionIR(title="S", level=1, blocks=[para, fig])],
        )
        NumberingPass().run(doc)
        assert para.inlines[0].target_id == "figure-1"
