"""Tests for final Markdown post-processing."""

from __future__ import annotations

from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter
from arxiv2md_beta.output.markdown_postprocess import _clean_math_latex, clean_markdown_output


class TestAnchorEmission:
    """Anchors are an emitter decision now — no text-layer stripping.

    The old _strip_anchor_tags tests pinned the emit-then-remove round trip;
    the equivalent guarantees are: anchors present when enabled, absent
    (with fragment links flattened to their text) when disabled.
    """

    def test_anchor_kept_when_enabled(self) -> None:
        from arxiv2md_beta.ir import DocumentIR, PaperMetadata, SectionIR

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t"), sections=[SectionIR(title="Intro", level=1, anchor="S1")]
        )
        out = MarkdownEmitter(include_anchors=True).emit(doc)
        assert '<a id="S1"></a>' in out

    def test_anchor_and_fragment_link_dropped_when_disabled(self) -> None:
        from arxiv2md_beta.ir import BlockQuoteIR, DocumentIR, LinkIR, PaperMetadata, ParagraphIR, SectionIR, TextIR

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t"),
            sections=[
                SectionIR(
                    title="Intro",
                    level=1,
                    anchor="S1",
                    blocks=[
                        ParagraphIR(inlines=[TextIR(text="see ")]),
                        BlockQuoteIR(
                            blocks=[
                                ParagraphIR(
                                    inlines=[LinkIR(kind="internal", target_id="S1", inlines=[TextIR(text="Self ref")])]
                                )
                            ]
                        ),
                    ],
                )
            ],
        )
        out = MarkdownEmitter(include_anchors=False).emit(doc)
        assert "<a id=" not in out
        assert "](#S1)" not in out
        assert "see" in out

    def test_anchor_kept_via_settings(self, monkeypatch) -> None:
        # include_anchors=None defers to settings.output.include_anchors
        from arxiv2md_beta.ir import DocumentIR, PaperMetadata, SectionIR
        from arxiv2md_beta.settings import get_settings

        s = get_settings().model_copy(deep=True)
        s.output.include_anchors = True
        monkeypatch.setattr("arxiv2md_beta.settings.get_settings", lambda: s)
        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="t"), sections=[SectionIR(title="Intro", level=1, anchor="S1")]
        )
        out = MarkdownEmitter().emit(doc)
        assert '<a id="S1"></a>' in out


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
    def test_anchors_pass_through_untouched(self) -> None:
        """Anchor handling is the emitter's decision.

        The text layer neither strips nor adds ``<a id>`` tags anymore.
        """
        text = '<a id="S1"></a>\n# Intro\n\ngood$C_{\\text{gen}}\\,$nice'
        result = clean_markdown_output(text)
        assert '<a id="S1"></a>' in result
        assert "good $C_{\\text{gen}}$ nice" in result

    def test_adds_spaces_around_inline_math_only_for_words(self) -> None:
        text = "answer$x$is here, and ($x$) works."
        result = clean_markdown_output(text)
        assert "answer $x$ is here" in result
        assert "($x$) works" in result

    def test_cleans_display_math(self) -> None:
        text = "$$\nx\\,\n$$"
        result = clean_markdown_output(text)
        assert "$$\nx\n$$" in result

    def test_preserves_indentation_for_display_math_in_lists(self) -> None:
        text = "1. item\n\n    $$\n    x\\,\n    $$\n\n2. next"
        result = clean_markdown_output(text)
        assert "    $$\n    x\n    $$" in result


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
        result = clean_markdown_output(text)
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
        result = clean_markdown_output(text)
        assert result == "run `$HOME $USER` and `$x$` now\n"

    def test_tilde_fence_untouched(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "~~~\n$5 $$ 10\n~~~\n"
        assert "$5 $$ 10" in clean_markdown_output(text)

    def test_unclosed_fence_safe(self) -> None:
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "intro\n\n```python\nx = '$5 and $10'\n"
        result = clean_markdown_output(text)
        assert "x = '$5 and $10'" in result

    def test_content_after_closed_fence_still_processed(self) -> None:
        """Postprocessing must resume after a *closed* fence.

        Regression: the closing-fence regex was built as an f-string, so
        "{0,7}" became a format field and every fence read as unclosed —
        silently exempting the rest of the document from all rules.
        """
        from arxiv2md_beta.output.markdown_postprocess import clean_markdown_output

        text = "```\ncode $5 here\n```\n\nanswer$x$is here\n"
        result = clean_markdown_output(text)
        assert "answer $x$ is here" in result  # post-fence content still cleaned
        inner = result.split("```\n", 1)[1].split("\n```", 1)[0]
        assert inner == "code $5 here"


class TestMathScannerProtections:
    """audit4 PR1.3: the ``$`` scanner must not rewrite link URLs or table rows."""

    def test_link_url_with_dollars_untouched(self) -> None:
        text = "see [x](https://a.com/$b$c?q=$d) now\n"
        assert clean_markdown_output(text) == text

    def test_image_url_with_dollars_untouched(self) -> None:
        text = "![chart](https://img.com/$x$.png)\n"
        assert clean_markdown_output(text) == text

    def test_link_alt_text_with_dollars_untouched(self) -> None:
        text = "[$a$ label](https://a.com/x)\n"
        assert clean_markdown_output(text) == text

    def test_table_row_conditional_probability_untouched(self) -> None:
        """Row structure must stay byte-stable (no injected ``$`` spacing)."""
        text = "| model | value |\n| --- | --- |\n| m1 | $P(a|b)$ |\n"
        assert clean_markdown_output(text) == text

    def test_table_row_display_dollars_not_expanded_multiline(self) -> None:
        """``$$…$$`` in a cell must not expand into a table-tearing block."""
        text = "| $$(x)$$ | y |\n| --- | --- |\n"
        result = clean_markdown_output(text)
        assert result.count("\n") == 2
        assert "| $$(x)$$ | y |" in result

    def test_prose_dollar_pairing_still_works_outside_links_and_tables(self) -> None:
        text = "cost $5 and $10 total, but $x$ is math\n"
        result = clean_markdown_output(text)
        assert "$5 and $10" in result
        assert "$x$" in result

    def test_math_outside_table_still_cleaned(self) -> None:
        text = "good$C_{\\text{gen}}\\,$nice\n"
        result = clean_markdown_output(text)
        assert "good $C_{\\text{gen}}$ nice" in result


def test_clean_markdown_output_lifts_fences_once(monkeypatch) -> None:
    """audit5 X6: one cleanup pass must scan for fences once, not per sub-pass.

    clean_markdown_output, _strip_anchor_tags and _clean_math_and_spacing
    each lifted the (already fence-free) text themselves — 3 full-text line
    loops per finalize.
    """
    import re

    from arxiv2md_beta.output import markdown_postprocess as mp

    calls = {"n": 0}
    real = mp.protect_fenced_code

    def counting(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(mp, "protect_fenced_code", counting)
    text = "intro\n\n```python\ncode $1$\n```\n\n$$x=1$$ done **b**\n\n| a | b |\n|---|---|\n| $1$ | 2 |"
    out = mp.clean_markdown_output(text)
    assert calls["n"] == 1
    assert "```python" in out and "code $1$" in out  # fence intact
    assert not re.search(r"\n{3,}", out)


def test_many_rejected_flanking_candidates_stay_literal() -> None:
    """audit5 X7 characterization: candidates rejected by the flanking rule.

    Each rejected candidate used to re-join the whole token window
    (O(n²) on pathological input); output must stay identical.
    """
    from arxiv2md_beta.output.markdown_postprocess import _clean_math_and_spacing

    src = "p $q $ r $ s $ end"
    assert _clean_math_and_spacing(src) == src
    src2 = "a $1 $2 $3 $4 $5 b"
    assert _clean_math_and_spacing(src2) == src2
