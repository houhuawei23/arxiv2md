"""Tests for the shared IR finalization tail (emit_split_markdown)."""

from __future__ import annotations

from arxiv2md_beta.ingestion.ir_finalize import emit_split_markdown
from arxiv2md_beta.ir import ParagraphIR, SectionIR, TextIR
from arxiv2md_beta.ir.document import DocumentIR, PaperMetadata


def _doc() -> DocumentIR:
    return DocumentIR(
        metadata=PaperMetadata(arxiv_id="2501.00000", title="T", parser="html"),
        front_matter=[ParagraphIR(inlines=[TextIR(text="TITLE-BLOCK-FIGURE")])],
        abstract=[ParagraphIR(inlines=[TextIR(text="ABSTRACT-TEXT")])],
        sections=[
            SectionIR(
                level=2,
                title="Introduction",
                blocks=[ParagraphIR(inlines=[TextIR(text="intro body")])],
                children=[],
            ),
            SectionIR(
                level=2,
                title="References",
                blocks=[ParagraphIR(inlines=[TextIR(text="- ref one")])],
                children=[],
            ),
            SectionIR(
                level=2,
                title="Appendix A",
                blocks=[ParagraphIR(inlines=[TextIR(text="appendix body")])],
                children=[],
            ),
        ],
    )


def test_sidecars_do_not_repeat_front_matter_or_abstract() -> None:
    """Regression (audit4 B3): front matter leaked into the sidecars."""
    doc = _doc()
    main, refs, appendix = emit_split_markdown(
        doc,
        reference_section_titles=["References"],
        include_anchors=False,
    )
    assert "ABSTRACT-TEXT" in main
    assert "TITLE-BLOCK-FIGURE" in main  # front matter belongs to the main doc
    assert refs is not None and "ref one" in refs
    assert "ABSTRACT-TEXT" not in refs and "TITLE-BLOCK-FIGURE" not in refs
    assert appendix is not None and "appendix body" in appendix
    assert "ABSTRACT-TEXT" not in appendix and "TITLE-BLOCK-FIGURE" not in appendix


def test_input_doc_is_not_mutated() -> None:
    """Regression (audit4 P3): emit_split_markdown mutated the doc in place."""
    doc = _doc()
    before_sections = doc.sections
    before_abstract_len = len(doc.abstract)
    before_front_len = len(doc.front_matter)

    emit_split_markdown(doc, reference_section_titles=["References"], include_anchors=False)

    assert doc.sections is before_sections
    assert len(doc.sections) == 3
    assert len(doc.abstract) == before_abstract_len == 1
    assert len(doc.front_matter) == before_front_len == 1
    # Section content survives intact (split works on the original list).
    assert [s.title for s in doc.sections] == ["Introduction", "References", "Appendix A"]
