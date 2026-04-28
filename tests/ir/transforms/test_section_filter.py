"""Tests for SectionFilterPass."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
    TextIR,
)
from arxiv2md_beta.ir.transforms.section_filter import SectionFilterPass


@pytest.fixture
def doc() -> DocumentIR:
    return DocumentIR(
        metadata=PaperMetadata(arxiv_id="test"),
        sections=[
            SectionIR(title="1. Introduction", level=1, struct_id="sec_0", blocks=[]),
            SectionIR(title="2. Methods", level=1, struct_id="sec_1", blocks=[]),
            SectionIR(title="3. Results", level=1, struct_id="sec_2", blocks=[]),
            SectionIR(title="4. Conclusion", level=1, struct_id="sec_3", blocks=[]),
        ],
    )


class TestSectionFilterPass:
    def test_exclude_sections(self, doc):
        SectionFilterPass(mode="exclude", selected=["Methods", "Results"]).run(doc)
        titles = [s.title for s in doc.sections]
        assert "2. Methods" not in titles
        assert "3. Results" not in titles
        assert "1. Introduction" in titles
        assert "4. Conclusion" in titles

    def test_include_sections(self, doc):
        SectionFilterPass(mode="include", selected=["Introduction", "Conclusion"]).run(doc)
        titles = [s.title for s in doc.sections]
        assert len(titles) == 2
        assert "1. Introduction" in titles
        assert "4. Conclusion" in titles

    def test_no_matches_include(self, doc):
        SectionFilterPass(mode="include", selected=["nonexistent"]).run(doc)
        assert len(doc.sections) == 0

    def test_no_matches_exclude(self, doc):
        SectionFilterPass(mode="exclude", selected=["nonexistent"]).run(doc)
        assert len(doc.sections) == 4  # all kept

    def test_case_insensitive(self, doc):
        SectionFilterPass(mode="exclude", selected=["methods"]).run(doc)
        titles = [s.title for s in doc.sections]
        assert "2. Methods" not in titles
