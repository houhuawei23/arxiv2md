"""Tests for FigureReorderPass."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    FigureIR,
    ImageRefIR,
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
                    title="Test", level=1,
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
                    title="Test", level=1,
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
                    title="Test", level=1,
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
                    title="Test", level=1,
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
