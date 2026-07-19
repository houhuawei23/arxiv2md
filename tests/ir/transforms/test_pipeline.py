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
from arxiv2md_beta.ir.transforms.numbering import NumberingPass, SectionNumberingPass
from arxiv2md_beta.ir.transforms.section_filter import SectionFilterPass
from arxiv2md_beta.ir.transforms.figure_reorder import FigureReorderPass
from arxiv2md_beta.ir.transforms.anchor import AnchorPass


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
    with_filter = build_default_pipeline(
        parser="html", section_filter_mode="exclude", selected_sections=["References"]
    )
    assert "SectionFilterPass" not in _types(no_filter)
    assert "SectionFilterPass" in _types(with_filter)


def test_remove_refs_adds_exclude_filter():
    pipeline = build_default_pipeline(
        parser="html", remove_refs=True, reference_section_titles=["References", "Bibliography"]
    )
    filter_passes = [p for p in pipeline._passes if isinstance(p, SectionFilterPass)]  # noqa: SLF001
    assert any(p.mode == "exclude" and p.selected == ["References", "Bibliography"] for p in filter_passes)


def test_canonical_order_preserved():
    """Numbering before FigureReorder before Anchor; SectionNumbering after Numbering."""
    pipeline = build_default_pipeline(parser="latex", selected_sections=["X"])
    types = _types(pipeline)
    assert types.index("NumberingPass") < types.index("SectionNumberingPass")
    assert types.index("SectionNumberingPass") < types.index("FigureReorderPass")
    assert types.index("FigureReorderPass") < types.index("AnchorPass")
