"""audit5 S6 PR6.10: _wrap_line must hard-break over-wide tokens.

URLs, unspaced formulas and whole CJK paragraphs overflowed as single
enormous lines inside list items (10k+ character lines).
"""

from __future__ import annotations

from arxiv2md_beta.ir.emitters.markdown import _wrap_line


class TestWrapLineOverwideTokens:
    def test_normal_wrap_unchanged(self) -> None:
        line = "- " + "word " * 30
        out = _wrap_line(line, "  ")
        assert out[0].startswith("- word")
        assert all(len(x) <= 100 for x in out)
        assert all(x.startswith("  ") for x in out[1:])

    def test_short_line_passthrough(self) -> None:
        assert _wrap_line("- short", "  ") == ["- short"]

    def test_ascii_overwide_token_hard_broken(self) -> None:
        line = "- " + "x" * 250
        out = _wrap_line(line, "  ")
        assert all(len(x) <= 100 for x in out), [len(x) for x in out]
        assert sum(x.count("x") for x in out) == 250  # no content lost

    def test_cjk_paragraph_broken_into_lines(self) -> None:
        line = "- " + "汉" * 3000
        out = _wrap_line(line, "  ")
        assert len(out) > 30
        assert all(len(x) <= 100 for x in out), [len(x) for x in out][:5]
        # No content lost: every Han character survives the break.
        assert sum(x.count("汉") for x in out) == 3000

    def test_mixed_words_and_overwide_url(self) -> None:
        url = "https://example.com/" + "a" * 180
        line = f"- see {url} for details"
        out = _wrap_line(line, "    ")
        assert all(len(x) <= 100 for x in out), [len(x) for x in out]
        assert out[0] == "- see"
        assert sum(x.count("a") for x in out) == line.count("a")  # no content lost
        assert " for details" in out[-1]

    def test_long_line_of_normal_words_still_word_wraps(self) -> None:
        line = "- " + " ".join(["word"] * 60)
        out = _wrap_line(line, "  ")
        assert len(out) >= 3
        assert all(x.strip() for x in out)
