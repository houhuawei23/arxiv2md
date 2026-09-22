"""Tests for FigureReorderPass."""

from __future__ import annotations

from arxiv2md_beta.ir import (
    DocumentIR,
    FigureIR,
    ImageRefIR,
    LinkIR,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
    TextIR,
)
from arxiv2md_beta.ir.transforms.figure_reorder import FigureReorderPass


class TestFigureReorderPass:
    def test_figure_moves_to_first_citation(self):
        """Figure after its citation should move right after the citing paragraph."""
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Test",
                    level=1,
                    blocks=[
                        ParagraphIR(inlines=[TextIR(text="See Figure 1 for details.")]),
                        ParagraphIR(inlines=[TextIR(text="Another paragraph.")]),
                        FigureIR(
                            figure_id="figure-1",
                            images=[ImageRefIR(src="./fig1.png")],
                            caption=[TextIR(text="Figure 1")],
                        ),
                    ],
                ),
            ],
        )

        FigureReorderPass().run(doc)

        blocks = doc.sections[0].blocks
        # Figure should now be right after the citing paragraph
        assert blocks[0].type == "paragraph"
        assert blocks[1].type == "figure"
        assert blocks[2].type == "paragraph"

    def test_figure_stays_if_before_citation(self):
        """Figure before its citation should stay in place."""
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Test",
                    level=1,
                    blocks=[
                        FigureIR(
                            figure_id="figure-1",
                            images=[ImageRefIR(src="./fig1.png")],
                            caption=[TextIR(text="Figure 1")],
                        ),
                        ParagraphIR(inlines=[TextIR(text="See Figure 1 for details.")]),
                    ],
                ),
            ],
        )

        FigureReorderPass().run(doc)

        blocks = doc.sections[0].blocks
        assert blocks[0].type == "figure"
        assert blocks[1].type == "paragraph"

    def test_no_citation_no_move(self):
        """Figure with no citation should stay in place."""
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Test",
                    level=1,
                    blocks=[
                        ParagraphIR(inlines=[TextIR(text="No figure mentioned.")]),
                        FigureIR(
                            figure_id="figure-1",
                            images=[ImageRefIR(src="./fig1.png")],
                        ),
                    ],
                ),
            ],
        )

        FigureReorderPass().run(doc)
        blocks = doc.sections[0].blocks
        assert blocks[0].type == "paragraph"
        assert blocks[1].type == "figure"  # stays

    def test_multiple_figures(self):
        """Multiple figures reorder correctly."""
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Test",
                    level=1,
                    blocks=[
                        ParagraphIR(inlines=[TextIR(text="Ref Figure 2 and Figure 1.")]),
                        FigureIR(figure_id="figure-1", images=[ImageRefIR(src="./a.png")]),
                        FigureIR(figure_id="figure-2", images=[ImageRefIR(src="./b.png")]),
                    ],
                ),
            ],
        )

        FigureReorderPass().run(doc)

        blocks = doc.sections[0].blocks
        # Both figures should be after the citing paragraph
        types = [b.type for b in blocks]
        assert types[0] == "paragraph"  # citing paragraph
        assert types[1] in ("figure",)  # moved figure
        assert types[2] in ("figure",)  # moved figure

    def test_abbreviated_fig_citation_matches(self):
        """Regression: ``Fig. 1`` and ``Fig 1`` must match, not only ``Figure 1``."""
        for citation in ("See Fig. 1 for details.", "As shown in Fig 1,"):
            doc = DocumentIR(
                metadata=PaperMetadata(arxiv_id="test"),
                sections=[
                    SectionIR(
                        title="Test",
                        level=1,
                        blocks=[
                            ParagraphIR(inlines=[TextIR(text=citation)]),
                            ParagraphIR(inlines=[TextIR(text="Filler.")]),
                            FigureIR(
                                figure_id="figure-1",
                                images=[ImageRefIR(src="./fig1.png")],
                                caption=[TextIR(text="Figure 1")],
                            ),
                        ],
                    ),
                ],
            )
            FigureReorderPass().run(doc)
            blocks = doc.sections[0].blocks
            assert [b.type for b in blocks] == [
                "paragraph",
                "figure",
                "paragraph",
            ], f"Fig.-style citation did not trigger reorder: {citation!r}"

    def test_citation_inside_math_is_seen(self):
        """Regression: citations embedded in MathIR.latex must be visible to matching.

        Layout: [paragraph(cite), filler, figure]. If the math citation is seen,
        the figure moves to right after the paragraph → [paragraph, figure, filler].
        If unseen (old hasattr behavior that skipped MathIR), no move occurs and the
        figure stays at the end. This distinguishes the two.
        """
        from arxiv2md_beta.ir import MathIR

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Test",
                    level=1,
                    blocks=[
                        ParagraphIR(inlines=[MathIR(latex=r"\ref{Figure 3}")]),
                        ParagraphIR(inlines=[TextIR(text="filler")]),
                        FigureIR(figure_id="figure-3", images=[ImageRefIR(src="./f3.png")]),
                    ],
                ),
            ],
        )
        FigureReorderPass().run(doc)
        blocks = doc.sections[0].blocks
        assert [b.type for b in blocks] == ["paragraph", "figure", "paragraph"]

    def test_citation_in_abstract_reorders(self):
        """Abstract citations should reorder abstract blocks (abstract IS processed)."""
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            abstract=[
                ParagraphIR(inlines=[TextIR(text="See Figure 5.")]),
                FigureIR(figure_id="figure-5", images=[ImageRefIR(src="./f5.png")]),
            ],
            sections=[],
        )
        FigureReorderPass().run(doc)
        assert [b.type for b in doc.abstract] == ["paragraph", "figure"]


class TestFigureReorderLaTeXAndPlural:
    def _doc_with(self, blocks: list) -> DocumentIR:
        return DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[SectionIR(title="T", level=1, blocks=blocks, children=[])],
        )

    def test_plural_figures_citation_matches(self) -> None:
        """audit4 S4.1: plural "Figures 3 and 4" must count as a citation."""
        from arxiv2md_beta.ir.transforms.figure_reorder import FigureReorderPass as P

        # Figures start at the END: they only land right after the citing
        # paragraph if the plural reference was actually matched.
        doc = self._doc_with(
            [
                ParagraphIR(inlines=[TextIR(text="As shown in Figures 3 and 4, the trend holds.")]),
                ParagraphIR(inlines=[TextIR(text="tail")]),
                FigureIR(figure_id="figure-3", images=[ImageRefIR(src="./f3.png")], caption=[]),
                FigureIR(figure_id="figure-4", images=[ImageRefIR(src="./f4.png")], caption=[]),
            ]
        )
        P().run(doc)
        blocks = doc.sections[0].blocks
        assert blocks[1].type == "figure" and blocks[1].figure_id == "figure-3"
        assert blocks[2].type == "figure" and blocks[2].figure_id == "figure-4"

    def test_latex_label_link_citation_moves_figure(self) -> None:
        """audit4 S4.1: LaTeX label-ref links must count as citations."""
        from arxiv2md_beta.ir.transforms.figure_reorder import FigureReorderPass as P

        doc = self._doc_with(
            [
                ParagraphIR(
                    inlines=[
                        TextIR(text="See "),
                        LinkIR(kind="internal", target_id="fig:setup", url=None, inlines=[TextIR(text="3")]),
                        TextIR(text=" for the setup."),
                    ]
                ),
                ParagraphIR(inlines=[TextIR(text="tail")]),
                FigureIR(
                    figure_id="fig:setup",
                    label="fig:setup",
                    anchor="fig:setup",
                    images=[ImageRefIR(src="./setup.png")],
                    caption=[],
                ),
            ]
        )
        P().run(doc)
        blocks = doc.sections[0].blocks
        assert blocks[1].type == "figure" and blocks[1].figure_id == "fig:setup"
        assert blocks[2].type == "paragraph"


class TestDuplicateFigureIds:
    """audit5 I-7: duplicate caption ids must both stay reorder candidates.

    A fig_id-keyed dict let the later figure overwrite the earlier, which
    then never moved to its citation.
    """

    def test_body_and_appendix_figure_both_reorder(self):
        # Same flat section: citation of Figure 1 sits between two paragraphs;
        # figure-1 #1 (body) comes after it and must move up even though a
        # second figure-1 (appendix copy) appears later.
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="S",
                    level=1,
                    blocks=[
                        ParagraphIR(inlines=[TextIR(text="Intro.")]),
                        ParagraphIR(inlines=[TextIR(text="See Figure 1 now.")]),
                        FigureIR(
                            figure_id="figure-1",
                            images=[ImageRefIR(src="./body.png")],
                            caption=[TextIR(text="Figure 1: body")],
                        ),
                        ParagraphIR(inlines=[TextIR(text="Filler.")]),
                        FigureIR(
                            figure_id="figure-1",
                            images=[ImageRefIR(src="./appendix.png")],
                            caption=[TextIR(text="Figure 1: appendix duplicate")],
                        ),
                    ],
                ),
            ],
        )
        sec = doc.sections[0]
        FigureReorderPass().run(doc)
        blocks = sec.blocks
        # the BODY figure moved directly after its citation
        assert blocks[2].caption[0].text == "Figure 1: body"
        assert blocks[1].inlines[0].text == "See Figure 1 now."
