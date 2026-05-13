"""Tests for LaTeX parser utilities."""

from __future__ import annotations

import pytest

from arxiv2md_beta.latex.parser import _beautify_math_display


class TestBeautifyMathDisplay:
    """Tests for _beautify_math_display."""

    def test_inline_display_math_gets_newlines(self):
        """Inline $$...$$ should be moved to its own lines."""
        text = "some text $$a + b$$ more text"
        result = _beautify_math_display(text)
        assert result == "some text\n\n$$\na + b\n$$\n\nmore text"

    def test_display_math_on_own_lines_unchanged(self):
        """Already block-level math should stay valid."""
        text = "para\n\n$$\nmath\n$$\n\npara"
        result = _beautify_math_display(text)
        assert result == "para\n\n$$\nmath\n$$\n\npara"

    def test_trailing_text_after_closing_dollars(self):
        """$$ on same line as trailing text must be split."""
        text = "via: $$\n\n     \\mathcal{L} = a\n b\n $$ where $x$ is..."
        result = _beautify_math_display(text)
        assert "via:\n\n$$\n\\mathcal{L}" in result
        assert "$$\n\nwhere $x$ is..." in result

    def test_multiple_inline_blocks(self):
        """Multiple inline $$ blocks are all fixed."""
        text = "text $$a$$ mid $$b$$ end"
        result = _beautify_math_display(text)
        assert result.count("$$\n") == 4  # each block has opening and closing $$

    def test_math_block_at_start_of_string(self):
        """Block at string start should not add leading newlines."""
        text = "$$a + b$$ text"
        result = _beautify_math_display(text)
        assert result.startswith("$$\n")
        assert "$$\n\ntext" in result

    def test_math_block_at_end_of_string(self):
        """Block at string end should not add trailing newlines."""
        text = "text $$a + b$$"
        result = _beautify_math_display(text)
        assert result.endswith("$$")
        assert "text\n\n$$\na + b\n$$" == result

    def test_excessive_internal_newlines_collapsed(self):
        """More than two internal newlines are collapsed."""
        text = "$$\na\n\n\n\nb\n$$"
        result = _beautify_math_display(text)
        assert "\n\n\n" not in result
        assert "$$\na\n\nb\n$$" == result
