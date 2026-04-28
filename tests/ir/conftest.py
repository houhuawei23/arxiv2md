"""Shared fixtures for IR tests."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    AuthorIR,
    BlockQuoteIR,
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
    RuleIR,
    SectionIR,
    TableIR,
    TextIR,
)


@pytest.fixture
def minimal_doc() -> DocumentIR:
    """Smallest meaningful DocumentIR."""
    return DocumentIR(
        metadata=PaperMetadata(arxiv_id="2501.12345", title="Test Paper"),
        abstract=[
            ParagraphIR(inlines=[TextIR(text="This is the abstract.")])
        ],
        sections=[
            SectionIR(
                title="Introduction",
                level=1,
                struct_id="sec_0",
                blocks=[
                    ParagraphIR(inlines=[TextIR(text="Hello world.")])
                ],
            )
        ],
    )


@pytest.fixture
def complex_doc() -> DocumentIR:
    """DocumentIR with a wide variety of block and inline types.

    Structure
    ---------
    - Abstract: 1 paragraph
    - Section 1 (Introduction): paragraph, heading, figure, code block
    - Section 1.1 (Background): paragraph with rich inlines, blockquote
    - Section 2 (Results): table, equation, list, rule
    """
    return DocumentIR(
        metadata=PaperMetadata(
            arxiv_id="2501.12345",
            arxiv_version="v2",
            title="A Comprehensive Study of IR Systems",
            authors=[AuthorIR(name="Alice Foo"), AuthorIR(name="Bob Bar")],
            submission_date="20250115",
            parser="html",
        ),
        abstract=[
            ParagraphIR(
                section_id="abstract",
                order_index=0,
                inlines=[
                    TextIR(text="We present a novel "),
                    EmphasisIR(style="bold", inlines=[TextIR(text="IR system")]),
                    TextIR(text=" for academic papers."),
                ],
            ),
        ],
        sections=[
            SectionIR(
                title="1. Introduction",
                level=1,
                struct_id="sec_0",
                blocks=[
                    ParagraphIR(
                        id="sec_0:b0:paragraph",
                        section_id="sec_0",
                        order_index=0,
                        inlines=[
                            TextIR(text="This is the first paragraph with "),
                            LinkIR(
                                kind="external",
                                url="https://example.com",
                                inlines=[TextIR(text="a link")],
                            ),
                            TextIR(text=" and "),
                            MathIR(latex="E=mc^2"),
                            TextIR(text=" inline math."),
                        ],
                    ),
                    HeadingIR(
                        id="sec_0:b1:heading",
                        section_id="sec_0",
                        order_index=1,
                        level=2,
                        inlines=[TextIR(text="Motivation")],
                    ),
                    ParagraphIR(
                        id="sec_0:b2:paragraph",
                        section_id="sec_0",
                        order_index=2,
                        inlines=[TextIR(text="Our motivation is clear.")],
                    ),
                    FigureIR(
                        id="sec_0:b3:figure",
                        section_id="sec_0",
                        order_index=3,
                        figure_id="figure-1",
                        anchor="figure-1",
                        label="fig:overview",
                        images=[
                            ImageRefIR(src="./images/overview.png", alt="System overview"),
                        ],
                        caption=[
                            TextIR(text="Figure 1: "),
                            EmphasisIR(style="italic", inlines=[TextIR(text="System overview")]),
                        ],
                    ),
                    CodeIR(
                        id="sec_0:b4:code",
                        section_id="sec_0",
                        order_index=4,
                        language="python",
                        text="print('Hello, world!')\n",
                    ),
                ],
                children=[
                    SectionIR(
                        title="1.1 Background",
                        level=2,
                        struct_id="sec_0_0",
                        blocks=[
                            ParagraphIR(
                                id="sec_0_0:b0:paragraph",
                                section_id="sec_0_0",
                                order_index=0,
                                inlines=[
                                    TextIR(text="Rich text with "),
                                    EmphasisIR(
                                        style="italic",
                                        inlines=[TextIR(text="italic")],
                                    ),
                                    TextIR(text=" and "),
                                    EmphasisIR(
                                        style="bold",
                                        inlines=[TextIR(text="bold")],
                                    ),
                                    TextIR(text=" formatting, plus a reference "),
                                    LinkIR(
                                        kind="internal",
                                        target_id="figure-1",
                                        inlines=[TextIR(text="Figure 1")],
                                    ),
                                    TextIR(text="."),
                                ],
                            ),
                            BlockQuoteIR(
                                id="sec_0_0:b1:blockquote",
                                section_id="sec_0_0",
                                order_index=1,
                                blocks=[
                                    ParagraphIR(
                                        inlines=[TextIR(text="This is a blockquote.")]
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            SectionIR(
                title="2. Results",
                level=1,
                struct_id="sec_1",
                blocks=[
                    TableIR(
                        id="sec_1:b0:table",
                        section_id="sec_1",
                        order_index=0,
                        table_id="table-1",
                        label="tab:results",
                        headers=[
                            [TextIR(text="Method")],
                            [TextIR(text="Score")],
                        ],
                        rows=[
                            [[TextIR(text="Baseline")], [TextIR(text="0.75")]],
                            [[TextIR(text="Ours")], [TextIR(text="0.92")]],
                        ],
                        caption=[
                            TextIR(text="Table 1: "),
                            EmphasisIR(style="bold", inlines=[TextIR(text="Results comparison")]),
                        ],
                    ),
                    EquationIR(
                        id="sec_1:b1:equation",
                        section_id="sec_1",
                        order_index=1,
                        latex="\\mathcal{L} = -\\sum_{i} y_i \\log(\\hat{y}_i)",
                        equation_number="(1)",
                        label="eq:loss",
                        anchor="eq:loss",
                    ),
                    ListIR(
                        id="sec_1:b2:list",
                        section_id="sec_1",
                        order_index=2,
                        ordered=False,
                        items=[
                            [ParagraphIR(inlines=[TextIR(text="First key finding")])],
                            [ParagraphIR(inlines=[TextIR(text="Second key finding")])],
                            [
                                ParagraphIR(inlines=[TextIR(text="Third with sub-items")]),
                                ListIR(
                                    ordered=False,
                                    items=[
                                        [ParagraphIR(inlines=[TextIR(text="Sub-item A")])],
                                        [ParagraphIR(inlines=[TextIR(text="Sub-item B")])],
                                    ],
                                ),
                            ],
                        ],
                    ),
                    RuleIR(
                        id="sec_1:b3:rule",
                        section_id="sec_1",
                        order_index=3,
                    ),
                    ParagraphIR(
                        id="sec_1:b4:paragraph",
                        section_id="sec_1",
                        order_index=4,
                        inlines=[TextIR(text="After the horizontal rule.")],
                    ),
                ],
            ),
        ],
    )
