"""Tests for SectionFilterPass."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    PaperMetadata,
    SectionIR,
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


class TestIncludeKeepsParentOfMatchedChild:
    """Include mode keeps a parent whose child matched (legacy tree semantics)."""

    def _doc(self):
        from arxiv2md_beta.ir import DocumentIR, PaperMetadata, ParagraphIR, SectionIR

        return DocumentIR(
            metadata=PaperMetadata(arxiv_id="test"),
            sections=[
                SectionIR(
                    title="Introduction",
                    level=1,
                    blocks=[ParagraphIR(inlines=[])],
                    children=[
                        SectionIR(title="Datasets", level=2, blocks=[ParagraphIR(inlines=[])]),
                        SectionIR(title="Metrics", level=2, blocks=[ParagraphIR(inlines=[])]),
                    ],
                ),
                SectionIR(title="Methods", level=1, blocks=[ParagraphIR(inlines=[])]),
            ],
        )

    def test_parent_kept_with_only_matched_child(self):
        doc = self._doc()
        SectionFilterPass(mode="include", selected=["Datasets"]).run(doc)
        assert [s.title for s in doc.sections] == ["Introduction"]
        intro = doc.sections[0]
        assert [c.title for c in intro.children] == ["Datasets"]

    def test_parent_dropped_when_no_child_matches(self):
        doc = self._doc()
        SectionFilterPass(mode="include", selected=["Methods"]).run(doc)
        assert [s.title for s in doc.sections] == ["Methods"]

    def test_exclude_mode_unaffected(self):
        doc = self._doc()
        SectionFilterPass(mode="exclude", selected=["Datasets"]).run(doc)
        assert [s.title for s in doc.sections] == ["Introduction", "Methods"]
        assert [c.title for c in doc.sections[0].children] == ["Metrics"]
