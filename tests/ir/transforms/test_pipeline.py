"""Tests for the canonical default-pipeline factory.

Locks the fixes from the 2026-07-19 refactor (docs/REVIEW_2026-07-19.md Phase 4):
* SectionNumberingPass runs for LaTeX only (was missing on the local LaTeX
  archive path -> numbering diverged between the two LaTeX paths).
* SectionFilterPass runs only when sections are selected (local archives
  previously skipped filtering entirely, silently ignoring --sections).
* remove_refs adds the references-exclude filter.
"""

from __future__ import annotations

from arxiv2md_beta.ir.transforms import build_default_pipeline
from arxiv2md_beta.ir.transforms.section_filter import SectionFilterPass


def _types(pipeline) -> list[str]:
    return [type(p).__name__ for p in pipeline._passes]  # noqa: SLF001


def test_latex_pipeline_includes_section_numbering():
    pipeline = build_default_pipeline(parser="latex")
    types = _types(pipeline)
    assert "SectionNumberingPass" in types


def test_html_pipeline_omits_section_numbering():
    pipeline = build_default_pipeline(parser="html")
    types = _types(pipeline)
    assert "SectionNumberingPass" not in types


def test_filter_only_added_when_sections_selected():
    no_filter = build_default_pipeline(parser="html", selected_sections=[])
    with_filter = build_default_pipeline(parser="html", section_filter_mode="exclude", selected_sections=["References"])
    assert "SectionFilterPass" not in _types(no_filter)
    assert "SectionFilterPass" in _types(with_filter)


def test_remove_refs_adds_exclude_filter():
    pipeline = build_default_pipeline(
        parser="html", remove_refs=True, reference_section_titles=["References", "Bibliography"]
    )
    filter_passes = [p for p in pipeline._passes if isinstance(p, SectionFilterPass)]  # noqa: SLF001
    assert any(p.mode == "exclude" and p.selected == ["References", "Bibliography"] for p in filter_passes)


def test_canonical_order_preserved():
    """Lock canonical pass order.

    SectionNumbering runs BEFORE the filter (struct_id selection in
    --sections needs the ids it assigns; a dropped section takes its number
    prefix with it, so "filtered sections are not numbered" still holds).
    Numbering before FigureReorder; FigureReorder last (anchors merged into
    NumberingPass).
    """
    pipeline = build_default_pipeline(parser="latex", selected_sections=["X"])
    types = _types(pipeline)
    assert types.index("SectionNumberingPass") < types.index("SectionFilterPass")
    assert types.index("SectionFilterPass") < types.index("NumberingPass")
    assert types.index("NumberingPass") < types.index("FigureReorderPass")
    assert "AnchorPass" not in types


class TestStructIdSelectionAfterNumbering:
    """The filter must run AFTER SectionNumberingPass.

    Regression: it used to run before, so struct_id-based --sections
    selection could never match.
    """

    def _latex_doc(self):
        from arxiv2md_beta.ir import LaTeXBuilder

        tex = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\section{Intro}\nIntro body.\n"
            "\\section{Method}\nMethod body.\n"
            "\\section{Results}\nResults body.\n"
            "\\end{document}"
        )
        return LaTeXBuilder().build(tex, arxiv_id="test")

    def test_filter_by_struct_id_finds_sections(self):
        from arxiv2md_beta.ir.transforms import build_default_pipeline

        doc = self._latex_doc()
        pipeline = build_default_pipeline(
            parser="latex",
            section_filter_mode="include",
            selected_sections=["sec_2"],
        )
        pipeline.run(doc)
        titles = [s.title for s in doc.sections]
        assert any("Method" in t for t in titles)
        assert not any("Results" in t for t in titles)
        assert not any("Intro" in t for t in titles)

    def test_exclude_by_numbered_title_still_matches(self):
        from arxiv2md_beta.ir.transforms import build_default_pipeline

        doc = self._latex_doc()
        pipeline = build_default_pipeline(parser="latex", selected_sections=["Results"])
        pipeline.run(doc)
        titles = [s.title for s in doc.sections]
        assert not any("Results" in t for t in titles)
        assert any("Method" in t for t in titles)


class TestStructIdDedup:
    def test_unnumbered_children_do_not_collide(self):
        from arxiv2md_beta.ir import DocumentIR, PaperMetadata, SectionIR
        from arxiv2md_beta.ir.transforms.numbering import SectionNumberingPass

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t", parser="latex"),
            sections=[
                SectionIR(
                    title="Appendix Overview",
                    level=1,
                    unnumbered=True,
                    blocks=[],
                    children=[SectionIR(title="Details", level=2, blocks=[])],
                ),
                SectionIR(title="Real First", level=1, blocks=[]),
            ],
        )
        SectionNumberingPass().run(doc)

        def ids(sections):
            out = []
            for s in sections:
                out.append(s.struct_id)
                out.extend(ids(s.children))
            return out

        all_ids = ids(doc.sections)
        assert len(all_ids) == len(set(all_ids)), f"duplicate struct_ids: {all_ids}"

    def test_json_fill_never_duplicates_pass_ids(self):
        from arxiv2md_beta.ir import SectionIR
        from arxiv2md_beta.ir.emitters.json_emitter import _assign_struct_ids

        doc_sections = [
            SectionIR(title="1 Intro", level=1, struct_id="sec_1", blocks=[]),
            SectionIR(title="References", level=1, blocks=[]),  # unnumbered
        ]
        _assign_struct_ids(doc_sections)
        assert doc_sections[0].struct_id == "sec_1"
        # Positional fill for index 1 would be "sec_1" — must get a suffix.
        assert doc_sections[1].struct_id != "sec_1"
        assert doc_sections[1].struct_id.startswith("sec_1-") or doc_sections[1].struct_id.startswith("sec_")
