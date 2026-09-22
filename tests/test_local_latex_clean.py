r"""Tests for the LaTeX text cleaner used by local ingestions (audit4 B5).

The old regex removed a command *together with its brace argument*, so
``our method is \textbf{fast} and robust`` lost the word "fast" from
extracted titles/abstracts.
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.ingestion.local import _clean_latex_text, _strip_latex_commands


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Content-bearing commands keep their argument.
        (r"our method is \textbf{fast} and robust", "our method is fast and robust"),
        (r"\emph{nested} words", "nested words"),
        (r"\textit{a} \textsc{b}", "a b"),
        # Nested braces peel recursively.
        (r"\textbf{\itshape fast}", "fast"),
        (r"\textbf{a \emph{b} c}", "a b c"),
        # Decoration commands lose their argument.
        (r"before \hspace{1em} after", "before after"),
        (r"\vspace{2mm}text", "text"),
        (r"\label{sec:one}body", "body"),
        # Bare commands without arguments vanish.
        (r"a \alpha b", "a b"),
        # Escaped literals survive as the literal character.
        (r"100\% done \& more", r"100% done & more"),
        (r"cost is \$5", "cost is $5"),
        # Double backslash is a line break -> space.
        (r"one\\two", "one two"),
        # Bare brace groups keep their contents.
        ("{braced} word", "braced word"),
        # Unbalanced input must not loop forever or crash; the inner text of
        # a truncated group is preserved (data retention over tidiness).
        (r"\textbf{unclosed", "unclosed"),
    ],
)
def test_strip_latex_commands(raw: str, expected: str) -> None:
    assert _strip_latex_commands(raw) == expected


def test_clean_latex_text_keeps_command_argument() -> None:
    assert _clean_latex_text(r"our method is \textbf{fast} and robust") == "our method is fast and robust"


def test_clean_latex_text_collapses_whitespace() -> None:
    assert _clean_latex_text("a\n  b\t c") == "a b c"
