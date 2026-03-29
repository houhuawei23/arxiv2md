"""Tests for Markdown conversion."""

from __future__ import annotations

import base64
from pathlib import Path

from arxiv2md_beta.html.markdown import convert_fragment_to_markdown


def test_convert_simple_html():
    """Test converting simple HTML to Markdown."""
    html = "<p>Hello <strong>world</strong></p>"
    md = convert_fragment_to_markdown(html)
    assert "Hello" in md
    assert "**world**" in md


def test_convert_figure_with_image_map():
    """Test converting figure with image map."""
    html = """
    <figure>
        <img src="test.png" alt="Test Image" />
        <figcaption>Test Caption</figcaption>
    </figure>
    """
    image_map = {0: Path("images/figure_1.png")}
    md = convert_fragment_to_markdown(html, image_map=image_map)
    assert "figure_1.png" in md
    assert "Test Caption" in md


def test_multi_panel_figure_emits_all_rasters_before_single_caption():
    """One <figure> with nested panels shares one caption; each <img> consumes one image_map slot."""
    html = """
    <figure id="S4.F2" class="ltx_figure">
    <figure class="ltx_figure_panel"><img src="dgm_comparisons.png" alt=""/></figure>
    <figure class="ltx_figure_panel"><img src="dgm_comparisons_polyglot.png" alt=""/></figure>
    <figcaption>Figure 2: Left SWE-bench and right Polyglot.</figcaption>
    </figure>
    """
    image_map = {
        0: Path("images/dgm_comparisons.png"),
        1: Path("images/dgm_comparisons_polyglot.png"),
    }
    counter = [0]
    md = convert_fragment_to_markdown(html, image_map=image_map, figure_counter=counter)
    assert '<div align="center">' in md
    assert 'width="45%"' in md
    assert 'src="images/dgm_comparisons.png"' in md
    assert 'src="images/dgm_comparisons_polyglot.png"' in md
    assert "Left SWE-bench" in md
    assert counter[0] == 2


def test_figure_image_stem_map_overrides_mismatched_index():
    """When HTML figure order differs from TeX order, match by <img src> basename."""
    html = """
    <figure class="ltx_figure">
        <img src="https://arxiv.org/html/2311.15127/figures/teaser_figure_v3.001.jpeg" alt="t" />
        <figcaption class="ltx_caption">Figure 1: Teaser caption.</figcaption>
    </figure>
    """
    wrong = Path("images/sota_i2v_baselines.png")
    right = Path("images/teaser_figure_v3.001.jpeg")
    image_map = {0: wrong}
    stem_map = {"teaser_figure_v3.001": right, "teaser_figure_v3.001.jpeg": right}
    md = convert_fragment_to_markdown(
        html,
        image_map=image_map,
        image_stem_map=stem_map,
        figure_counter=[0],
    )
    assert "teaser_figure_v3.001" in md
    assert "sota_i2v_baselines" not in md
    assert "Teaser caption" in md


def test_convert_table():
    """Test converting table to Markdown."""
    html = """
    <table>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
    </table>
    """
    md = convert_fragment_to_markdown(html)
    assert "| A | B |" in md
    assert "| 1 | 2 |" in md


def test_ltx_table_figure_uses_span_tabular_not_html_table():
    """ar5iv often emits tabulars as span.ltx_tabular (no <table>); figure must still serialize rows."""
    html = """
    <figure class="ltx_table" id="S1.T1">
    <figcaption class="ltx_caption"><span class="ltx_tag_table">Table 1</span>: Caption here.</figcaption>
    <span class="ltx_tabular ltx_align_middle" id="T1">
    <span class="ltx_thead">
    <span class="ltx_tr">
    <span class="ltx_td ltx_th">A1</span>
    <span class="ltx_td ltx_th">B1</span>
    </span>
    </span>
    <span class="ltx_tbody">
    <span class="ltx_tr">
    <span class="ltx_td">a</span>
    <span class="ltx_td">b</span>
    </span>
    </span>
    </span>
    </figure>
    """
    md = convert_fragment_to_markdown(html)
    assert "| A1 | B1 |" in md
    assert "| a | b |" in md
    assert "Caption here" in md
    # Old bug: caption-only fallback produced "> Table: Table 1: ..."
    assert "> Table: Table" not in md


def test_convert_inline_svg_figure(tmp_path: Path):
    """Test converting a figure that contains an inline SVG, saving to images/."""
    html = """
    <figure>
        <span class="ltx_inline-block">
            <svg width="10" height="10"><circle cx="5" cy="5" r="3"/></svg>
        </span>
        <figcaption>Figure 1: A simple SVG circle.</figcaption>
    </figure>
    """
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    md = convert_fragment_to_markdown(html, images_dir=images_dir)
    # SVG should be saved as a separate file under images/
    svg_files = list(images_dir.glob("*.svg"))
    assert svg_files, "Expected an SVG file to be created in images/ directory"
    # Markdown should reference the saved SVG
    assert "images/" in md and ".svg" in md
    assert "A simple SVG circle." in md


def test_ltx_algorithm_figure_serializes_listing():
    """ar5iv uses figure.ltx_algorithm (not only ltx_float_algorithm); listing must emit code."""
    html = """
    <figure class="ltx_float ltx_algorithm" id="S4.F1">
    <figcaption class="ltx_caption"><span class="ltx_tag">Algorithm 1</span>: Test.</figcaption>
    <div class="ltx_listing ltx_lst_numbers_left">
    <div class="ltx_listingline"><span class="ltx_tag">1</span> line one</div>
    <div class="ltx_listingline"><span class="ltx_tag">2</span> line two</div>
    </div>
    </figure>
    """
    md = convert_fragment_to_markdown(html)
    assert '<a id="algorithm-1"></a>' in md
    assert "```text" in md
    assert "line one" in md
    assert "line two" in md


def test_paragraph_ltx_listing_decodes_base64_data_uri():
    """Inline listing may expose plain text via data:text/plain;base64,... in ltx_listing_data."""
    raw = b"def foo():\n    return 1\n"
    b64 = base64.b64encode(raw).decode("ascii")
    html = f"""
    <div class="ltx_para">
    <div class="ltx_listing">
    <div class="ltx_listing_data">
    <a href="data:text/plain;charset=utf-8;base64,{b64}">data</a>
    </div>
    </div>
    </div>
    """
    md = convert_fragment_to_markdown(html)
    assert "```text" in md
    assert "def foo():" in md
    assert "return 1" in md
