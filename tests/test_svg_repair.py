"""Tests for ar5iv label-less SVG repair (arXiv LaTeXML TikZ label drop).

The oxide LaTeXML build sometimes emits ``ltx_picture`` SVGs whose
foreignObject prose labels are all empty — the figure rasterizes as bare
shapes. Detection and substitution live in ``html.svg_repair``.
"""

from __future__ import annotations

from arxiv2md_beta.html.svg_repair import (
    labelless_picture_svg_spans,
    substitute_svgs_from_ar5iv,
)


def _picture_svg(svg_id: str, label: str) -> str:
    label_html = (
        f"<foreignObject><span class='ltx_p'>{label}</span></foreignObject>"
        if label
        else "<foreignObject><span class='ltx_p'></span></foreignObject>"
    )
    return f"<svg class='ltx_picture' id='{svg_id}' width='100' height='50'>{label_html}<path d='M0 0 L10 10'/></svg>"


def _doc(svg: str) -> str:
    return (
        f"<html><body><article class='ltx_document'><figure class='ltx_figure'>{svg}</figure></article></body></html>"
    )


def test_labelless_svg_detected() -> None:
    html = _doc(_picture_svg("S1.F3.pic1", ""))
    spans = labelless_picture_svg_spans(html)
    assert [svg_id for svg_id, _, _ in spans] == ["S1.F3.pic1"]


def test_labeled_svg_not_flagged() -> None:
    html = _doc(_picture_svg("S1.F3.pic1", "a deployed system runs a term"))
    assert labelless_picture_svg_spans(html) == []


def test_math_only_labels_count_as_labelless() -> None:
    r"""Math-only labels must count as label-less.

    The oxide build keeps math labels (glyph + annotation) while dropping
    prose — math must be excluded from the word count or the breakage is
    masked (arXiv 2609.06396v2 S4.F6: 54 math 'words', zero English).
    """
    math_label = (
        r"<math><semantics><mi>D</mi>"
        r"<annotation encoding='application/x-tex'>\mathcal{D}</annotation></semantics></math>"
    )
    svg = (
        "<svg class='ltx_picture' id='S4.F6.pic1' width='100' height='50'>"
        f"<foreignObject><span class='ltx_p'>{math_label}</span></foreignObject>"
        "<path d='M0 0'/></svg>"
    )
    assert [svg_id for svg_id, _, _ in labelless_picture_svg_spans(_doc(svg))] == ["S4.F6.pic1"]


def test_non_picture_and_nested_svgs_ignored() -> None:
    chrome_icon = "<svg viewBox='0 0 24 24'><path d='M0 0'/></svg>"
    outer = (
        "<svg class='ltx_picture' id='S4.F6.pic1' width='100' height='50'>"
        "<foreignObject><span class='ltx_p'>panel label here</span></foreignObject>"
        "<svg class='ltx_picture' id='S4.F6.pic1.pic1'><path d='M0 0'/></svg>"
        "</svg>"
    )
    html = _doc(chrome_icon + outer)
    assert labelless_picture_svg_spans(html) == []


def test_substitution_swaps_broken_svg_for_ar5iv_counterpart() -> None:
    native = _doc(_picture_svg("S1.F3.pic1", ""))
    ar5iv = _doc(_picture_svg("S1.F3.pic1", "the evidence panel with real labels"))
    new_html, replaced = substitute_svgs_from_ar5iv(native, ar5iv)
    assert replaced == 1
    assert "real labels" in new_html


def test_substitution_skips_when_ar5iv_also_broken() -> None:
    native = _doc(_picture_svg("S1.F3.pic1", ""))
    ar5iv = _doc(_picture_svg("S1.F3.pic1", ""))
    new_html, replaced = substitute_svgs_from_ar5iv(native, ar5iv)
    assert replaced == 0
    assert new_html == native


def test_substitution_skips_unknown_ids() -> None:
    native = _doc(_picture_svg("S1.F3.pic1", ""))
    ar5iv = _doc(_picture_svg("S9.F99.pic1", "unrelated labels"))
    _, replaced = substitute_svgs_from_ar5iv(native, ar5iv)
    assert replaced == 0


def test_multiple_svgs_replaced_independently() -> None:
    native = _doc(_picture_svg("S1.F3.pic1", "") + _picture_svg("S4.F6.pic1", "already labeled fine"))
    ar5iv = _doc(
        _picture_svg("S1.F3.pic1", "restored panel labels here") + _picture_svg("S4.F6.pic1", "already labeled fine")
    )
    new_html, replaced = substitute_svgs_from_ar5iv(native, ar5iv)
    assert replaced == 1
    assert "restored panel labels here" in new_html
    assert "already labeled fine" in new_html
