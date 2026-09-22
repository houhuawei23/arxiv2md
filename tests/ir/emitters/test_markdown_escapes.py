"""Escape-contract tests for the MarkdownEmitter image/figure paths.

Regression coverage for audit4 B1: the single-image path used to interpolate
alt/src verbatim (``![{alt}]({src})``), so a caption-derived alt containing
``]`` — or a src containing spaces/parens — produced broken Markdown. The
multi-image and grid paths render raw ``<img>`` HTML, where an unescaped ``"``
in alt/src truncates the attribute.
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import EmphasisIR, FigureIR, ImageRefIR, MathIR, TableIR, TextIR
from arxiv2md_beta.ir.emitters.escapes import escape_math_pipes
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter


@pytest.fixture
def emitter() -> MarkdownEmitter:
    return MarkdownEmitter()


# ── Single image (GFM syntax) ─────────────────────────────────────────


class TestSingleImageEscaping:
    def test_alt_with_brackets_is_escaped(self, emitter: MarkdownEmitter) -> None:
        fig = FigureIR(images=[ImageRefIR(src="images/fig.png", alt="Accuracy [best] per model")], caption=[])
        out = emitter._emit_block(fig)
        assert out == "![Accuracy \\[best\\] per model](images/fig.png)"

    def test_alt_with_backslash_is_escaped(self, emitter: MarkdownEmitter) -> None:
        fig = FigureIR(images=[ImageRefIR(src="fig.png", alt=r"a\b")], caption=[])
        assert emitter._emit_block(fig) == r"![a\\b](fig.png)"

    def test_src_with_spaces_and_parens_is_percent_encoded(self, emitter: MarkdownEmitter) -> None:
        fig = FigureIR(images=[ImageRefIR(src="figs/my (1).png", alt="panel")], caption=[])
        out = emitter._emit_block(fig)
        assert out == "![panel](figs/my%20%281%29.png)"

    def test_matches_image_ref_inline_behavior(self, emitter: MarkdownEmitter) -> None:
        """Single-image output must equal the inline image_ref rendering."""
        alt, src = "Accuracy [best]", "figs/my (1).png"
        from arxiv2md_beta.ir import ImageRefIR as Img

        fig_out = emitter._emit_block(FigureIR(images=[Img(src=src, alt=alt)], caption=[]))
        inline_out = emitter._emit_inline(Img(src=src, alt=alt))
        assert fig_out == inline_out


# ── Multi-image strip (raw HTML attributes) ───────────────────────────


class TestMultiImageAttributeEscaping:
    def test_alt_with_double_quote_cannot_break_attribute(self, emitter: MarkdownEmitter) -> None:
        fig = FigureIR(
            images=[
                ImageRefIR(src="a.png", alt='A "quoted" panel'),
                ImageRefIR(src="b.png", alt="B"),
            ],
            caption=[],
        )
        out = emitter._emit_block(fig)
        assert 'alt="A &quot;quoted&quot; panel"' in out
        # The attribute boundary survives: exactly one alt attribute per img.
        assert out.count('alt="') == 2

    def test_src_with_double_quote_cannot_break_attribute(self, emitter: MarkdownEmitter) -> None:
        fig = FigureIR(
            images=[
                ImageRefIR(src='a"x.png', alt="A"),
                ImageRefIR(src="b.png", alt="B"),
            ],
            caption=[],
        )
        out = emitter._emit_block(fig)
        assert '<img src="a&quot;x.png"' in out

    def test_alt_with_angle_brackets_escaped(self, emitter: MarkdownEmitter) -> None:
        fig = FigureIR(
            images=[ImageRefIR(src="a.png", alt="<b>"), ImageRefIR(src="b.png", alt="B")],
            caption=[],
        )
        out = emitter._emit_block(fig)
        assert 'alt="&lt;b&gt;"' in out


# ── Grid figure (raw HTML attributes) ─────────────────────────────────


class TestGridImageAttributeEscaping:
    def test_alt_with_double_quote_cannot_break_attribute(self, emitter: MarkdownEmitter) -> None:
        grid = [
            [
                [ImageRefIR(src="a.png", alt='row "one"')],  # type: ignore[list-item]
            ]
        ]
        fig = FigureIR(grid=grid, caption=[TextIR(text="grid")], images=[])
        out = emitter._emit_block(fig)
        assert 'alt="row &quot;one&quot;"' in out


# ── Table cells: math pipes (audit4 B2) ───────────────────────────────


class TestTableCellMathPipes:
    def test_conditional_probability_pipe_becomes_vert(self, emitter: MarkdownEmitter) -> None:
        r"""A math pipe must become \vert, never \| (KaTeX reads \| as ‖)."""
        tbl = TableIR(headers=[[TextIR(text="model")]], rows=[[[MathIR(latex="P(a|b)")]]])
        out = emitter._emit_block(tbl)
        assert r"$P(a\vert b)$" in out
        assert r"\|" not in out

    def test_math_without_pipes_unchanged(self, emitter: MarkdownEmitter) -> None:
        tbl = TableIR(headers=[[TextIR(text="model")]], rows=[[[MathIR(latex="x^2 + 1")]]])
        out = emitter._emit_block(tbl)
        assert "$x^2 + 1$" in out

    def test_math_pipe_inside_emphasis_also_becomes_vert(self, emitter: MarkdownEmitter) -> None:
        cell = [EmphasisIR(style="bold", inlines=[MathIR(latex="P(a|b)")])]
        tbl = TableIR(headers=[[TextIR(text="model")]], rows=[[cell]])
        out = emitter._emit_block(tbl)
        assert r"$P(a\vert b)$" in out
        assert r"\|" not in out

    def test_prose_pipe_in_text_cell_still_escaped(self, emitter: MarkdownEmitter) -> None:
        tbl = TableIR(headers=[[TextIR(text="A | B")]], rows=[])
        out = emitter._emit_block(tbl)
        assert "| A \\| B |" in out


# ── Table cells: norm pipes survive (audit5 C3) ───────────────────────


class TestTableCellNormPipes:
    def test_norm_pipes_are_preserved(self, emitter: MarkdownEmitter) -> None:
        r"""audit5 C3: \| is the norm delimiter and must survive verbatim.

        The audit4 B2 fix replaced *every* pipe with \vert, degrading
        $\|x\|_F$ (matrix/norm notation) into a single-bar pair.
        """
        tbl = TableIR(headers=[[TextIR(text="model")]], rows=[[[MathIR(latex=r"\|x\|_F")]]])
        out = emitter._emit_block(tbl)
        assert r"$\|x\|_F$" in out

    def test_norm_and_conditional_in_one_cell(self, emitter: MarkdownEmitter) -> None:
        tbl = TableIR(headers=[[TextIR(text="m")]], rows=[[[MathIR(latex=r"\|x-y\| \cdot P(a|b)")]]])
        out = emitter._emit_block(tbl)
        assert r"\|x-y\|" in out
        assert r"P(a\vert b)" in out


class TestEscapeMathPipes:
    def test_bare_pipe_replaced(self) -> None:
        assert escape_math_pipes("P(a|b)") == r"P(a\vert b)"

    def test_norm_pipe_untouched(self) -> None:
        assert escape_math_pipes(r"\|x\|") == r"\|x\|"

    def test_double_backslash_then_pipe_is_bare(self) -> None:
        # 'a \\| b': the \\\\ row separator pairs up, so the pipe is bare.
        assert escape_math_pipes(r"a \\| b") == r"a \\\vert  b"

    def test_plain_text_passthrough(self) -> None:
        assert escape_math_pipes("no pipes at all") == "no pipes at all"


class TestInlineCodeDelims:
    """audit5 G2-3: inline code delimiters depend on the content."""

    def test_plain_text_single_backtick(self):
        from arxiv2md_beta.ir.emitters.escapes import inline_code_delims

        assert inline_code_delims("x = 1") == ("`", "`")

    def test_backtick_content_doubles(self):
        from arxiv2md_beta.ir.emitters.escapes import inline_code_delims

        assert inline_code_delims("a`b") == ("``", "``")

    def test_leading_backtick_gets_space_pad(self):
        from arxiv2md_beta.ir.emitters.escapes import inline_code_delims

        assert inline_code_delims("`cmd") == ("`` ", " ``")

    def test_emitted_span_round_trips(self):
        from arxiv2md_beta.ir import EmphasisIR, MarkdownEmitter, TextIR

        em = EmphasisIR(style="code", inlines=[TextIR(text="a`b")])
        out = MarkdownEmitter()._emit_inline(em)
        assert out == "``a`b``"


class TestEscapeLineStart:
    """audit5 G2-2: paragraph first lines must not read as block syntax."""

    def test_plain_text_untouched(self):
        from arxiv2md_beta.ir.emitters.escapes import escape_line_start

        assert escape_line_start("The method works.") == "The method works."

    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("# Not a heading", "\\# Not a heading"),
            ("### Deep", "\\### Deep"),
            ("- dash", "\\- dash"),
            ("* star", "\\* star"),
            ("+ plus", "\\+ plus"),
            ("> quote", "\\> quote"),
            ("---", "\\---"),
            ("1. ordered", "1\\. ordered"),
            ("2000. year list", "2000\\. year list"),
            ("3) paren form", "3\\) paren form"),
        ],
    )
    def test_block_syntax_openings_escaped(self, raw, escaped):
        from arxiv2md_beta.ir.emitters.escapes import escape_line_start

        assert escape_line_start(raw) == escaped

    def test_syntax_after_first_line_untouched(self):
        from arxiv2md_beta.ir.emitters.escapes import escape_line_start

        assert escape_line_start("a\n- b") == "a\n- b"

    def test_number_without_marker_untouched(self):
        from arxiv2md_beta.ir.emitters.escapes import escape_line_start

        assert escape_line_start("2000 years ago") == "2000 years ago"

    def test_paragraph_block_emission_uses_it(self):
        from arxiv2md_beta.ir import MarkdownEmitter, ParagraphIR, TextIR

        p = ParagraphIR(inlines=[TextIR(text="# Looks like a heading")])
        out = MarkdownEmitter()._emit_block(p)
        assert not out.startswith("#")
