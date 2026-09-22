"""Tests for final Markdown post-processing."""

from __future__ import annotations

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
    # Output is POSIX newline-terminated.
    assert cleaned.content == "$x$\n"
    # Output is POSIX newline-terminated.
    assert cleaned.content_references == "\n"
    assert cleaned.content_appendix is None


class TestCleanMathAndSpacingEdges:
    """Locked-in behavior of _clean_math_and_spacing on delimiter edge cases."""

    CASES = [
        ("a $x$ b", "a $x$ b"),
        ("unbalanced $ math", "unbalanced $ math"),
        ("$$display$$", "$$\ndisplay\n$$"),
        ("$$a$$ b $$c$$", "$$\na\n$$ b $$\nc\n$$"),
        ("$a$$b$", "$a$ $b$"),
        ("$$$", "$$$"),
        # The lone "$" between the two display blocks is literal; so is the
        # whitespace-flanked " b " after it (pandoc flanking rule).
        ("$$ a $$$ b $$", "$$\na\n$$$ b $$"),
        # Dollar amounts: no flanking-valid math pair, stays literal.
        ("price $5 and $10 total", "price $5 and $10 total"),
        ("$a\nb$ collapse", "$a b$ collapse"),
        ("$$\nx=1\n$$", "$$\nx=1\n$$"),
        ("  $$  \nx=1\n  $$  ", "  $$\nx=1\n$$  "),
    ]

    def test_edge_cases(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import _clean_math_and_spacing

        for source, expected in self.CASES:
            assert _clean_math_and_spacing(source) == expected, f"input: {source!r}"

    def test_empty_display_region_does_not_raise(self) -> None:
        # Regression: "$$$$$" (inline followed by an empty "$$$$" display
        # region) used to IndexError in the inline spacing lookahead.
        from arxiv2md_beta.output.markdown_postprocess import _clean_math_and_spacing

        assert _clean_math_and_spacing("$x$$$$$") == "$x$$$\n\n$$"
        assert _clean_math_and_spacing("$a$$$$") == "$a$$$$"


class TestFencedCodeProtection:
    """Postprocessing must never rewrite the contents of code blocks."""

    def test_fenced_dollars_and_table_untouched(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "```bash\nprice $5 and $10 total\n**Table 1: demo**\n\n\n\nkeep blank lines\n```\n"
        result = clean_markdown_output(text, include_anchors=False)
        inner = result.split("```bash\n", 1)[1].split("\n```", 1)[0]
        # Blank lines inside the fence survive the 3+ newline collapse.
        assert inner == "price $5 and $10 total\n**Table 1: demo**\n\n\n\nkeep blank lines"

    def test_fenced_code_survives_format_markdown_output(self) -> None:
        from arxiv2md_beta.output.markdown_utils import format_markdown_output

        text = "```\n**Table 1: demo**\n| a | b |\n```"
        assert format_markdown_output(text) == text

    def test_inline_code_dollars_untouched(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "run `$HOME $USER` and `$x$` now\n"
        result = clean_markdown_output(text, include_anchors=False)
        assert result == "run `$HOME $USER` and `$x$` now\n"

    def test_tilde_fence_untouched(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "~~~\n$5 $$ 10\n~~~\n"
        assert "$5 $$ 10" in clean_markdown_output(text, include_anchors=False)

    def test_unclosed_fence_safe(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "intro\n\n```python\nx = '$5 and $10'\n"
        result = clean_markdown_output(text, include_anchors=False)
        assert "x = '$5 and $10'" in result

    def test_content_after_closed_fence_still_processed(self) -> None:
        """Postprocessing must resume after a *closed* fence.

        Regression: the closing-fence regex was built as an f-string, so
        "{0,7}" became a format field and every fence read as unclosed —
        silently exempting the rest of the document from all rules.
        """
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "```\ncode $5 here\n```\n\nanswer$x$is here\n"
        result = clean_markdown_output(text, include_anchors=False)
        assert "answer $x$ is here" in result  # post-fence content still cleaned
        inner = result.split("```\n", 1)[1].split("\n```", 1)[0]
        assert inner == "code $5 here"


class TestMathScannerProtections:
    """audit4 PR1.3: the ``$`` scanner must not rewrite link URLs or table rows."""

    def test_link_url_with_dollars_untouched(self) -> None:
        text = "see [x](https://a.com/$b$c?q=$d) now\n"
        assert clean_markdown_output(text, include_anchors=False) == text

    def test_image_url_with_dollars_untouched(self) -> None:
        text = "![chart](https://img.com/$x$.png)\n"
        assert clean_markdown_output(text, include_anchors=False) == text

    def test_link_alt_text_with_dollars_untouched(self) -> None:
        text = "[$a$ label](https://a.com/x)\n"
        assert clean_markdown_output(text, include_anchors=False) == text

    def test_table_row_conditional_probability_untouched(self) -> None:
        """Row structure must stay byte-stable (no injected ``$`` spacing)."""
        text = "| model | value |\n| --- | --- |\n| m1 | $P(a|b)$ |\n"
        assert clean_markdown_output(text, include_anchors=False) == text

    def test_table_row_display_dollars_not_expanded_multiline(self) -> None:
        """``$$…$$`` in a cell must not expand into a table-tearing block."""
        text = "| $$(x)$$ | y |\n| --- | --- |\n"
        result = clean_markdown_output(text, include_anchors=False)
        assert result.count("\n") == 2
        assert "| $$(x)$$ | y |" in result

    def test_prose_dollar_pairing_still_works_outside_links_and_tables(self) -> None:
        text = "cost $5 and $10 total, but $x$ is math\n"
        result = clean_markdown_output(text, include_anchors=False)
        assert "$5 and $10" in result
        assert "$x$" in result

    def test_math_outside_table_still_cleaned(self) -> None:
        text = "good$C_{\\text{gen}}\\,$nice\n"
        result = clean_markdown_output(text, include_anchors=False)
        assert "good $C_{\\text{gen}}$ nice" in result
