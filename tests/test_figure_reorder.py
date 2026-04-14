"""Tests for figure reordering to first citation paragraph."""

from __future__ import annotations

from arxiv2md_beta.output.formatter import (
    _contains_figure_reference,
    _extract_figure_id_from_blocks,
    reorder_figures_to_first_reference,
)


def test_extract_figure_id_from_caption():
    """Extract figure ID from caption block."""
    blocks = ['<a id="figure-1"></a>', '![alt](path.png)', '> Figure 1: caption']
    assert _extract_figure_id_from_blocks(blocks) == "1"


def test_extract_figure_id_from_anchor_fallback():
    """Extract figure ID from anchor when caption is absent."""
    blocks = ['<a id="figure-3"></a>', '![alt](path.png)']
    assert _extract_figure_id_from_blocks(blocks) == "3"


def test_contains_figure_reference():
    """Detect various figure reference formats."""
    assert _contains_figure_reference("See Figure 1 for details.", "1")
    assert _contains_figure_reference("See Fig. 1 for details.", "1")
    assert _contains_figure_reference("See Fig 1 for details.", "1")
    assert _contains_figure_reference("See [Figure 1](#figure-1) for details.", "1")
    assert _contains_figure_reference("See [1](#figure-1) for details.", "1")
    assert not _contains_figure_reference("See Figure 2 for details.", "1")


def test_reorder_simple_figure_to_first_reference():
    """Simple figure should move to after its first citation paragraph."""
    markdown = (
        "<a id=\"figure-1\"></a>\n\n"
        "![ModalNet-21](images/ModalNet-21.png)\n\n"
        "> Figure 1: The Transformer - model architecture.\n\n"
        "Most competitive neural sequence transduction models have an "
        "encoder-decoder structure.\n\n"
        "The Transformer follows this overall architecture using stacked "
        "self-attention and point-wise, fully connected layers for both the "
        "encoder and decoder, shown in the left and right halves of "
        "Figure [1](#figure-1), respectively."
    )
    result = reorder_figures_to_first_reference(markdown)
    # The figure should now appear after the second paragraph (the one containing the citation)
    assert "shown in the left and right halves of Figure [1](#figure-1)" in result
    # Figure block should come after the citation paragraph
    citation_pos = result.find("shown in the left and right halves")
    figure_pos = result.find('<a id="figure-1"></a>')
    assert figure_pos > citation_pos
    # It should no longer be at the very beginning
    assert not result.startswith('<a id="figure-1"></a>')


def test_reorder_multi_panel_figure():
    """Multi-panel figure block moves as a single unit."""
    markdown = (
        "<a id=\"figure-2\"></a>\n\n"
        '![a](images/a.png)\n\n'
        '![b](images/b.png)\n\n'
        "> Figure 2: Two panels.\n\n"
        "Intro paragraph.\n\n"
        "See Figure 2 for the panels."
    )
    result = reorder_figures_to_first_reference(markdown)
    # Both images and caption should move together after citation paragraph
    intro_pos = result.find("Intro paragraph.")
    figure_pos = result.find('<a id="figure-2"></a>')
    assert figure_pos > intro_pos
    assert result.count("![a](images/a.png)") == 1
    assert result.count("![b](images/b.png)") == 1
    assert result.count("> Figure 2: Two panels.") == 1


def test_unreferenced_figure_stays_in_place():
    """Unreferenced figure should remain at its original position."""
    markdown = (
        "Paragraph one.\n\n"
        "<a id=\"figure-3\"></a>\n\n"
        "![img](images/img.png)\n\n"
        "> Figure 3: Unused figure.\n\n"
        "Paragraph two."
    )
    result = reorder_figures_to_first_reference(markdown)
    # Figure should stay between paragraph one and paragraph two
    pos1 = result.find("Paragraph one.")
    pos_fig = result.find('<a id="figure-3"></a>')
    pos2 = result.find("Paragraph two.")
    assert pos1 < pos_fig < pos2


def test_multiple_figures_only_first_reference_moves():
    """Each figure moves independently to its own first citation."""
    markdown = (
        "<a id=\"figure-1\"></a>\n\n"
        "![1](images/1.png)\n\n"
        "> Figure 1: First.\n\n"
        "<a id=\"figure-2\"></a>\n\n"
        "![2](images/2.png)\n\n"
        "> Figure 2: Second.\n\n"
        "See Figure 2 here.\n\n"
        "See Figure 1 here."
    )
    result = reorder_figures_to_first_reference(markdown)
    # Figure 2 should move after "See Figure 2 here."
    # Figure 1 should move after "See Figure 1 here."
    fig1_pos = result.find('<a id="figure-1"></a>')
    fig2_pos = result.find('<a id="figure-2"></a>')
    cite2_pos = result.find("See Figure 2 here.")
    cite1_pos = result.find("See Figure 1 here.")
    assert fig2_pos > cite2_pos
    assert fig1_pos > cite1_pos


def test_figure_without_id_ignored():
    """Figure without identifiable ID is left untouched."""
    markdown = (
        "![img](images/img.png)\n\n"
        "Paragraph with no reference."
    )
    result = reorder_figures_to_first_reference(markdown)
    # No caption or anchor with figure number, so it stays put
    assert result == markdown


def test_latex_style_figure_reference():
    """Support LaTeX-style markdown figure references."""
    markdown = (
        "<a id=\"figure-1\"></a>\n\n"
        "![img](images/img.png)\n\n"
        "> Figure 1: Caption.\n\n"
        "Refer to [Figure 1](#fig:architecture)."
    )
    result = reorder_figures_to_first_reference(markdown)
    cite_pos = result.find("Refer to [Figure 1](#fig:architecture).")
    fig_pos = result.find('<a id="figure-1"></a>')
    assert fig_pos > cite_pos


def test_figure_bracket_link_reference():
    """Match patterns like Figure [2](#figure-2)."""
    markdown = (
        '<a id="figure-2"></a>\n\n'
        '<div align="center">\n'
        '  <img src="images/a.png" width="45%" alt="a" />\n'
        '  <img src="images/b.png" width="45%" alt="b" />\n'
        '</div>\n\n'
        '> Figure 2: caption.\n\n'
        'Intro paragraph.\n\n'
        'See Figure [2](#figure-2) for details.'
    )
    result = reorder_figures_to_first_reference(markdown)
    cite_pos = result.find("See Figure [2](#figure-2) for details.")
    fig_pos = result.find('<a id="figure-2"></a>')
    assert fig_pos > cite_pos
    assert result.count('<div align="center">') == 1


def test_table_not_moved():
    """Tables should not be treated as figures and must stay in place."""
    markdown = (
        'Paragraph before.\n\n'
        '<a id="table-1"></a>\n\n'
        '> Table 1: Example table.\n\n'
        '| A | B |\n| --- | --- |\n| 1 | 2 |\n\n'
        'See Table 1 above.\n\n'
        'Paragraph after.'
    )
    result = reorder_figures_to_first_reference(markdown)
    pos_before = result.find("Paragraph before.")
    pos_table = result.find('<a id="table-1"></a>')
    pos_after = result.find("Paragraph after.")
    assert pos_before < pos_table < pos_after
    assert result.count("> Table 1: Example table.") == 1
