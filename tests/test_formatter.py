"""Tests for markdown formatter output layout."""

from __future__ import annotations

from arxiv2md_beta.output.formatter import format_paper
from arxiv2md_beta.schemas import SectionNode


def test_toc_uses_heading_links_and_includes_abstract() -> None:
    sections = [
        SectionNode(
            title="2 Related work",
            level=2,
            markdown="Related work intro.",
            children=[
                SectionNode(title="2.1 Imitation learning", level=3, markdown="IL body."),
                SectionNode(title="2.2 JEPA", level=3, markdown="JEPA body."),
            ],
        )
    ]

    result = format_paper(
        arxiv_id="2501.14622",
        version=None,
        title="Test",
        authors=[],
        abstract="Abstract body.",
        sections=sections,
        include_toc=True,
        include_abstract_in_tree=True,
        split_for_reference=False,
    )

    assert "## Contents" in result.content
    assert "- [Abstract](#abstract)" in result.content
    assert "- [2 Related work](#2-related-work)" in result.content
    assert "  - [2.1 Imitation learning](#21-imitation-learning)" in result.content


def test_section_with_children_renders_local_child_toc() -> None:
    sections = [
        SectionNode(
            title="2 Related work",
            level=2,
            markdown="Related work intro.",
            children=[
                SectionNode(title="2.1 Imitation learning", level=3, markdown="IL body."),
                SectionNode(title="2.2 JEPA", level=3, markdown="JEPA body."),
            ],
        )
    ]

    result = format_paper(
        arxiv_id="2501.14622",
        version=None,
        title="Test",
        authors=[],
        abstract=None,
        sections=sections,
        include_toc=False,
        include_abstract_in_tree=False,
        split_for_reference=False,
    )

    assert "## 2 Related work" in result.content
    assert "- [2.1 Imitation learning](#21-imitation-learning)" in result.content
    assert "- [2.2 JEPA](#22-jepa)" in result.content
    assert "### 2.1 Imitation learning" in result.content


def test_formatter_normalizes_double_bullet_markers() -> None:
    sections = [
        SectionNode(
            title="1 Intro",
            level=2,
            markdown="- • first point\n- • second point",
        )
    ]

    result = format_paper(
        arxiv_id="2501.14622",
        version=None,
        title="Test",
        authors=[],
        abstract=None,
        sections=sections,
        include_toc=False,
        include_abstract_in_tree=False,
        split_for_reference=False,
    )

    assert "- first point" in result.content
    assert "- second point" in result.content
    assert "- •" not in result.content


def test_formatter_deduplicates_nested_abstract_heading() -> None:
    sections: list[SectionNode] = []
    result = format_paper(
        arxiv_id="2501.14622",
        version=None,
        title="Test",
        authors=[],
        abstract="###### Abstract\n\nThis is the abstract body.",
        sections=sections,
        include_toc=False,
        include_abstract_in_tree=False,
        split_for_reference=False,
    )
    assert "## Abstract" in result.content
    assert "###### Abstract" not in result.content
    assert "This is the abstract body." in result.content
