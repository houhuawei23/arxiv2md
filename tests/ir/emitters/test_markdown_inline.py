"""Tests for MarkdownEmitter inline rendering."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    BreakIR,
    EmphasisIR,
    ImageRefIR,
    LinkIR,
    MathIR,
    ParagraphIR,
    RawInlineIR,
    SubscriptIR,
    SuperscriptIR,
    TextIR,
)
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter


@pytest.fixture
def emitter() -> MarkdownEmitter:
    return MarkdownEmitter()


class TestTextIR:
    def test_plain_text(self, emitter):
        assert emitter._emit_inline(TextIR(text="hello")) == "hello"

    def test_empty_text(self, emitter):
        assert emitter._emit_inline(TextIR(text="")) == ""


class TestEmphasisIR:
    def test_italic(self, emitter):
        n = EmphasisIR(style="italic", inlines=[TextIR(text="hi")])
        assert emitter._emit_inline(n) == "*hi*"

    def test_bold(self, emitter):
        n = EmphasisIR(style="bold", inlines=[TextIR(text="hi")])
        assert emitter._emit_inline(n) == "**hi**"

    def test_code(self, emitter):
        n = EmphasisIR(style="code", inlines=[TextIR(text="hi")])
        assert emitter._emit_inline(n) == "`hi`"

    def test_nested(self, emitter):
        n = EmphasisIR(
            style="bold",
            inlines=[
                TextIR(text="a"),
                EmphasisIR(style="italic", inlines=[TextIR(text="b")]),
            ],
        )
        assert emitter._emit_inline(n) == "**a*b***"


class TestLinkIR:
    def test_external_link(self, emitter):
        n = LinkIR(kind="external", url="https://x.com", inlines=[TextIR(text="x")])
        assert emitter._emit_inline(n) == "[x](https://x.com)"

    def test_internal_link(self, emitter):
        n = LinkIR(kind="internal", target_id="figure-1", inlines=[TextIR(text="Fig 1")])
        assert emitter._emit_inline(n) == "[Fig 1](#figure-1)"

    def test_citation_link(self, emitter):
        n = LinkIR(kind="citation", target_id="ref-1", inlines=[TextIR(text="[1]")])
        # target_id "ref-N" carries the bibitem position; emit numeric [N].
        assert emitter._emit_inline(n) == "[1]"

    def test_citation_link_linked(self):
        emitter = MarkdownEmitter(linked_citations=True)
        n = LinkIR(kind="citation", target_id="ref-1", inlines=[TextIR(text="[1]")])
        assert emitter._emit_inline(n) == "[1](#ref-1)"

    def test_citation_link_removed(self):
        emitter = MarkdownEmitter(remove_inline_citations=True)
        n = LinkIR(kind="citation", target_id="ref-1", inlines=[TextIR(text="[1]")])
        assert emitter._emit_inline(n) == ""

    def test_citation_without_target_id_removed(self):
        # No target_id: still a citation, still removed when flag set.
        emitter = MarkdownEmitter(remove_inline_citations=True)
        n = LinkIR(kind="citation", inlines=[TextIR(text="[2]")])
        assert emitter._emit_inline(n) == ""

    def test_link_no_url_no_target(self, emitter):
        n = LinkIR(kind="external", inlines=[TextIR(text="text")])
        assert emitter._emit_inline(n) == "text"


class TestMathIR:
    def test_inline_math(self, emitter):
        n = MathIR(latex="E=mc^2", display=False)
        assert emitter._emit_inline(n) == "$E=mc^2$"

    def test_display_math(self, emitter):
        n = MathIR(latex="E=mc^2", display=True)
        assert emitter._emit_inline(n) == "$$\nE=mc^2\n$$"


class TestImageRefIR:
    def test_image(self, emitter):
        n = ImageRefIR(src="./a.png", alt="alt text")
        result = emitter._emit_inline(n)
        assert "![alt text](./a.png" in result

    def test_image_with_dimensions(self, emitter):
        # GFM image syntax cannot carry width/height inside the URL parens;
        # emitting them there corrupted the link target, so they are dropped
        # (block figures keep sizing via the multi-image <img> path).
        n = ImageRefIR(src="./a.png", alt="img", width="100", height="200")
        result = emitter._emit_inline(n)
        assert result == "![img](./a.png)"


class TestSuperscriptIR:
    def test_superscript(self, emitter):
        n = SuperscriptIR(inlines=[TextIR(text="1")])
        assert emitter._emit_inline(n) == "<sup>1</sup>"


class TestSubscriptIR:
    def test_subscript(self, emitter):
        n = SubscriptIR(inlines=[TextIR(text="i")])
        assert emitter._emit_inline(n) == "<sub>i</sub>"


class TestBreakIR:
    def test_break(self, emitter):
        assert emitter._emit_inline(BreakIR()) == "\n"


class TestRawInlineIR:
    def test_raw_html(self, emitter):
        n = RawInlineIR(format="html", content="<b>x</b>")
        assert emitter._emit_inline(n) == "<b>x</b>"


class TestInlineComposition:
    def test_mixed_inlines(self, emitter):
        p = ParagraphIR(
            inlines=[
                TextIR(text="Hello "),
                EmphasisIR(style="bold", inlines=[TextIR(text="world")]),
                TextIR(text="!"),
            ]
        )
        assert emitter._emit_block(p) == "Hello **world**!"


class TestEscaping:
    """R2.4: link/image syntax must survive special characters."""

    def test_link_text_with_brackets(self, emitter) -> None:
        n = LinkIR(kind="external", url="https://a.b/c", inlines=[TextIR(text="a [b] c")])
        out = emitter._emit_inline(n)
        assert out.startswith("[a \\[b\\] c](")
        assert out.endswith("(https://a.b/c)".replace("(", "(")) or "(https://a.b/c)" in out

    def test_url_with_spaces_and_parens(self, emitter) -> None:
        n = LinkIR(kind="external", url="https://a.b/my file (1).pdf", inlines=[TextIR(text="doc")])
        out = emitter._emit_inline(n)
        assert "my%20file%20%281%29.pdf" in out
        assert " " not in out.rsplit("(", 1)[1]

    def test_image_alt_with_bracket(self, emitter) -> None:
        n = ImageRefIR(src="fig 1.png", alt="[Fig]")
        out = emitter._emit_inline(n)
        assert out == "![\\[Fig\\]](fig%201.png)"
