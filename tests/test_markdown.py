"""Tests for Markdown conversion."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.markdown import convert_fragment_to_markdown


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
