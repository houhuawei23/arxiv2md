"""Unit tests for the shared table span expansion (audit4 P2, audit5 G1-5)."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir.builders._table_spans import (
    MAX_COLSPAN,
    MAX_ROWSPAN,
    clamp_span,
    expand_table_spans,
)


def cell(text: str, colspan: int = 1, rowspan: int = 1):
    # inlines stand in as bare strings; expand_table_spans never inspects them
    return ([text], colspan, rowspan)


def texts(row: list[list]) -> list[str | None]:
    return [c[0] if c else None for c in row]


class TestClampSpan:
    def test_passthrough_int(self):
        assert clamp_span(3, MAX_COLSPAN) == 3

    def test_stringy_html_attr(self):
        assert clamp_span("2", MAX_COLSPAN) == 2

    def test_none_and_missing(self):
        assert clamp_span(None, MAX_COLSPAN) == 1

    def test_degenerate_zero(self):
        assert clamp_span(0, MAX_COLSPAN) == 1
        assert clamp_span("0", MAX_ROWSPAN) == 1

    def test_garbage(self):
        assert clamp_span("abc", MAX_COLSPAN) == 1
        assert clamp_span([], MAX_ROWSPAN) == 1

    def test_ceiling(self):
        assert clamp_span(9999, MAX_COLSPAN) == MAX_COLSPAN
        assert clamp_span(9999, MAX_ROWSPAN) == MAX_ROWSPAN


class TestExpandTableSpans:
    def test_no_spans_unchanged(self):
        grid = expand_table_spans([[cell("a"), cell("b")], [cell("c"), cell("d")]])
        assert [texts(r) for r in grid] == [["a", "b"], ["c", "d"]]

    def test_colspan_repeats_placeholder_copies(self):
        grid = expand_table_spans([[cell("wide", colspan=3), cell("end")]])
        assert texts(grid[0]) == ["wide", None, None, "end"]

    def test_rowspan_reserves_column_in_next_row(self):
        grid = expand_table_spans([[cell("a", rowspan=2), cell("b")], [cell("c")]])
        assert [texts(r) for r in grid] == [["a", "b"], [None, "c"]]

    def test_rowspan_mid_row_placeholder_position(self):
        grid = expand_table_spans([[cell("a"), cell("s", rowspan=2), cell("c")], [cell("a2"), cell("c2")]])
        assert [texts(r) for r in grid] == [
            ["a", "s", "c"],
            ["a2", None, "c2"],
        ]

    def test_rowspan_extends_past_row_end(self):
        # colspan-2 rowspan-2 cell claims two columns in the next row even
        # though that row has a single cell of its own.
        grid = expand_table_spans([[cell("w", colspan=2, rowspan=2), cell("z")], [cell("q")]])
        assert [texts(r) for r in grid] == [["w", None, "z"], [None, None, "q"]]

    def test_rowspan_expiring_after_three_rows(self):
        # rowspan=3 covers rows 0, 1 and 2 — the placeholder disappears only
        # in the row after the span is exhausted.
        grid = expand_table_spans(
            [
                [cell("a", rowspan=3), cell("b")],
                [cell("c")],
                [cell("d")],
                [cell("e")],
            ]
        )
        assert [texts(r) for r in grid] == [
            ["a", "b"],
            [None, "c"],
            [None, "d"],  # still inside the span
            ["e"],  # span exhausted: no placeholder
        ]

    def test_combined_spans_stay_rectangular(self):
        grid = expand_table_spans(
            [
                [cell("h1", colspan=2), cell("h3", rowspan=2)],
                [cell("a")],
                [cell("b"), cell("c"), cell("d")],
            ]
        )
        assert all(len(r) == 3 for r in grid)
        assert [texts(r) for r in grid] == [
            ["h1", None, "h3"],
            ["a", None, None],
            ["b", "c", "d"],
        ]

    def test_degenerate_spans_clamped(self):
        grid = expand_table_spans([[cell("a", colspan=999, rowspan=999), cell("b")], [cell("c")]])
        # colspan clamped to MAX_COLSPAN, rowspan to MAX_ROWSPAN — no explosion
        assert len(grid[0]) == MAX_COLSPAN + 1
        assert len(grid[1]) >= MAX_COLSPAN  # placeholders still keep columns aligned

    def test_empty_input(self):
        assert expand_table_spans([]) == []


@pytest.mark.parametrize("bad", [None, "x", 0, -3])
def test_clamp_never_returns_below_one(bad):
    assert clamp_span(bad, MAX_COLSPAN) >= 1
