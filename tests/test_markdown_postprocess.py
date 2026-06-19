"""Tests for final Markdown post-processing."""

from __future__ import annotations

import pytest

from arxiv2md_beta.output.markdown_postprocess import (
    _clean_math_latex,
    _remove_anchor_tags,
    clean_markdown_output,
)
from arxiv2md_beta.schemas import IngestionResult


class TestRemoveAnchors:
    def test_removes_inline_anchor(self) -> None:
        text = 'Hello\n\n<a id="S1"></a>\n\n# Intro'
        assert _remove_anchor_tags(text) == "Hello\n\n# Intro"

    def test_collapses_blank_lines(self) -> None:
        text = '<a id="figure-1"></a>\n\n\n\n![img](path.png)'
        assert _remove_anchor_tags(text) == "![img](path.png)"


class TestCleanMathLatex:
    def test_removes_trailing_thinspace(self) -> None:
        assert _clean_math_latex(r"C_{\text{gen}}\,") == r"C_{\text{gen}}"

    def test_removes_trailing_escaped_space(self) -> None:
        assert _clean_math_latex(r"x \ ") == r"x"

    def test_removes_multiple_trailing_space_commands(self) -> None:
        assert _clean_math_latex(r"x\,\;") == r"x"

    def test_leaves_internal_space_commands(self) -> None:
        assert _clean_math_latex(r"x\, + y") == r"x\, + y"


class TestCleanMarkdownOutput:
    def test_default_strips_anchors_and_cleans_math(self) -> None:
        text = '<a id="S1"></a>\n# Intro\n\ngood$C_{\\text{gen}}\\,$nice'
        result = clean_markdown_output(text, include_anchors=False)
        assert "<a id=" not in result
        assert "good $C_{\\text{gen}}$ nice" in result

    def test_keeps_anchors_when_requested(self) -> None:
        text = '<a id="S1"></a>\n# Intro\n\n$x$'
        result = clean_markdown_output(text, include_anchors=True)
        assert '<a id="S1"></a>' in result

    def test_adds_spaces_around_inline_math_only_for_words(self) -> None:
        text = "answer$x$is here, and ($x$) works."
        result = clean_markdown_output(text, include_anchors=False)
        assert "answer $x$ is here" in result
        assert "($x$) works" in result

    def test_cleans_display_math(self) -> None:
        text = "$$\nx\\,\n$$"
        result = clean_markdown_output(text, include_anchors=False)
        assert "$$\nx\n$$" in result

    def test_preserves_indentation_for_display_math_in_lists(self) -> None:
        text = "1. item\n\n    $$\n    x\\,\n    $$\n\n2. next"
        result = clean_markdown_output(text, include_anchors=False)
        assert "    $$\n    x\n    $$" in result


def test_apply_markdown_postprocessing() -> None:
    from arxiv2md_beta.output.markdown_postprocess import apply_markdown_postprocessing

    result = IngestionResult(
        summary="summary",
        sections_tree="tree",
        content='<a id="S1"></a>\n$x\\,$',
        content_references='<a id="ref-1"></a>',
        content_appendix=None,
    )
    cleaned = apply_markdown_postprocessing(result, include_anchors=False)
    assert "<a id=" not in cleaned.content
    assert cleaned.content == "$x$"
    assert cleaned.content_references == ""
    assert cleaned.content_appendix is None
