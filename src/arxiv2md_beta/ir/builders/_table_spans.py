"""Shared table span expansion for the HTML and LaTeX builders.

Pipe tables cannot express colspan/rowspan, so both builders materialize
spanned cells into a grid of single-column cells before emission: a
colspan-N cell is repeated N times (audit4 P2), and a rowspan-N cell
additionally reserves its column in the following N-1 rows via empty
placeholder cells (audit5 G1-5). Keeping the algorithm in one module stops
the builders' table walkers from diverging.
"""

from __future__ import annotations

# Sanity clamps: degenerate attributes (colspan="0", rowspan="9999") must not
# produce zero-width rows or thousands of padding cells.
MAX_COLSPAN = 16
MAX_ROWSPAN = 256

# A raw cell before span materialization: ``(inlines, colspan, rowspan)``.
RawCell = tuple[list, int, int]
RawRow = list[RawCell]


def clamp_span(raw: object, ceiling: int) -> int:
    """Clamp a span attribute (int from pandoc, stringy from HTML) to [1, ceiling]."""
    if raw is None:
        return 1
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return 1
    return max(1, min(value, ceiling))


def expand_table_spans(raw_rows: list[RawRow]) -> list[list[list]]:
    """Materialize colspan/rowspan cells into per-row placeholder cells.

    Each raw cell is ``(inlines, colspan, rowspan)``. Content stays in the
    first copy of a spanned cell; every additional column and every column
    reserved in later rows gets an empty ``[]`` placeholder, so the emitted
    pipe-table rows keep their column alignment.
    """
    # column index -> number of upcoming rows still occupied by an earlier
    # rowspan cell that already placed its content.
    pending: dict[int, int] = {}
    out: list[list[list]] = []

    for raw_row in raw_rows:
        cells: list[list] = []
        # Columns this row laid out (filled or placeholder) — each consumes
        # one row of whatever rowspan still held it at row start. Spans the
        # row itself just created only start counting with the NEXT row.
        held_at_start: set[int] = set(pending)
        touched: set[int] = set()
        col = 0

        for inlines, colspan, rowspan in raw_row:
            # Defensive clamp: callers clamp attribute input already, but the
            # shared function must stay safe on its own.
            colspan = clamp_span(colspan, MAX_COLSPAN)
            rowspan = clamp_span(rowspan, MAX_ROWSPAN)
            # Columns held by earlier rows' rowspans come first.
            while pending.get(col, 0) > 0:
                cells.append([])
                touched.add(col)
                col += 1
            cells.append(inlines)
            touched.add(col)
            # colspan repeats fill the row's own width (audit4 strategy).
            for offset in range(1, colspan):
                cells.append([])
                touched.add(col + offset)
            if rowspan > 1:
                for offset in range(colspan):
                    pending[col + offset] = max(pending.get(col + offset, 0), rowspan - 1)
            col += colspan

        # A rowspan can reach past this row's last cell (e.g. a wide header
        # cell spanning down). Pad every remaining column up to the farthest
        # held one — intermediate unheld columns are missing data anyway, and
        # the row must stay as wide as the grid.
        if pending:
            last_held = max(pending)
            while col <= last_held:
                cells.append([])
                touched.add(col)
                col += 1

        for c in touched:
            if c in held_at_start and c in pending:
                pending[c] -= 1
                if pending[c] <= 0:
                    del pending[c]
        out.append(cells)

    return out
