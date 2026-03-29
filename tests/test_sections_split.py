"""Tests for splitting sections at References / Bibliography."""

from __future__ import annotations

from arxiv2md_beta.html.sections import split_sections_at_reference
from arxiv2md_beta.schemas import SectionNode


def test_split_sections_at_reference_finds_references_heading() -> None:
    sections = [
        SectionNode(title="1 Introduction", level=2, markdown="a"),
        SectionNode(title="7 References", level=2, markdown="r"),
        SectionNode(title="Attention Visualizations", level=2, markdown="v"),
    ]
    main, refs, app = split_sections_at_reference(
        sections, reference_titles=["references", "bibliography"]
    )
    assert len(main) == 1
    assert main[0].title == "1 Introduction"
    assert len(refs) == 1
    assert refs[0].title == "7 References"
    assert len(app) == 1
    assert app[0].title == "Attention Visualizations"


def test_split_sections_bibliography_alias() -> None:
    sections = [
        SectionNode(title="2 Methods", level=2, markdown="m"),
        SectionNode(title="Bibliography", level=2, markdown="b"),
    ]
    main, refs, app = split_sections_at_reference(
        sections, reference_titles=["references", "bibliography"]
    )
    assert len(main) == 1
    assert len(refs) == 1
    assert refs[0].title == "Bibliography"
    assert app == []


def test_split_sections_no_match_returns_full_as_main() -> None:
    sections = [
        SectionNode(title="1 Introduction", level=2, markdown="a"),
        SectionNode(title="2 Related Work", level=2, markdown="b"),
    ]
    main, refs, app = split_sections_at_reference(
        sections, reference_titles=["references", "bibliography"]
    )
    assert len(main) == 2
    assert refs == []
    assert app == []


def test_split_sections_empty_reference_titles() -> None:
    sections = [
        SectionNode(title="1 Introduction", level=2, markdown="a"),
    ]
    main, refs, app = split_sections_at_reference(sections, reference_titles=[])
    assert len(main) == 1
    assert refs == []
    assert app == []
