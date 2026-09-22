"""Escape-contract tests for the MarkdownEmitter image/figure paths.

Regression coverage for audit4 B1: the single-image path used to interpolate
alt/src verbatim (``![{alt}]({src})``), so a caption-derived alt containing
``]`` — or a src containing spaces/parens — produced broken Markdown. The
multi-image and grid paths render raw ``<img>`` HTML, where an unescaped ``"``
in alt/src truncates the attribute.
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import FigureIR, ImageRefIR
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
        from arxiv2md_beta.ir import TextIR

        grid = [
            [
                [ImageRefIR(src="a.png", alt='row "one"')],  # type: ignore[list-item]
            ]
        ]
        fig = FigureIR(grid=grid, caption=[TextIR(text="grid")], images=[])
        out = emitter._emit_block(fig)
        assert 'alt="row &quot;one&quot;"' in out
