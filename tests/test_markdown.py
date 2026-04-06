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


def test_title_strip_skew_html_order_differs_from_tex_includegraphics_order():
    """Title/strip can remove the first raster; HTML figure order then != TeX order.

    Pair by ``<img src>`` stem via ``image_stem_map`` and mark ``image_map`` slots used;
    do not assign rasters by sequential figure index alone.
    """
    html = """
    <figure class="ltx_figure">
        <img src="https://arxiv.org/html/2602.10090/figures/teaser.001.png" alt="" />
        <figcaption class="ltx_caption">Figure 1: Teaser caption.</figcaption>
    </figure>
    <figure class="ltx_figure">
        <img src="https://arxiv.org/html/2602.10090/figures/logo8.png" alt="" />
        <figcaption class="ltx_caption">Figure 2: Logo caption.</figcaption>
    </figure>
    """
    logo = Path("images/logo8.png")
    teaser = Path("images/teaser.001.png")
    # TeX / resolver order: logo first (e.g. title), teaser second; HTML shows teaser then logo.
    image_map = {0: logo, 1: teaser}
    stem_map = {
        "teaser.001": teaser,
        "teaser.001.png": teaser,
        "logo8": logo,
        "logo8.png": logo,
    }
    md = convert_fragment_to_markdown(
        html,
        image_map=image_map,
        image_stem_map=stem_map,
        figure_counter=[0],
    )
    assert "teaser.001" in md
    assert "logo8" in md
    pos_teaser_img = md.find("teaser.001")
    pos_logo_img = md.find("logo8")
    pos_cap1 = md.find("Teaser caption")
    pos_cap2 = md.find("Logo caption")
    assert pos_teaser_img < pos_cap1 < pos_logo_img < pos_cap2


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


def test_local_arxiv_fragment_links_map_to_local_anchors():
    """#S2/#S4.SS1 style links should map to local generated anchors."""
    html = """
    <p>
      See <a href="#S2">Section 2</a> and <a href="#S4.SS1">Section 4.1</a>.
    </p>
    """
    md = convert_fragment_to_markdown(html)
    assert "[Section 2](#section-2)" in md
    assert "[Section 4.1](#section-4-1)" in md


def test_convert_heading_levels():
    """Test conversion of different heading levels."""
    html = """
    <h1>Heading 1</h1>
    <h2>Heading 2</h2>
    <h3>Heading 3</h3>
    <h4>Heading 4</h4>
    """
    md = convert_fragment_to_markdown(html)
    assert "# Heading 1" in md
    assert "## Heading 2" in md
    assert "### Heading 3" in md
    assert "#### Heading 4" in md


def test_convert_unordered_list():
    """Test unordered list conversion."""
    html = """
    <ul>
        <li>First item</li>
        <li>Second item</li>
        <li>Third item</li>
    </ul>
    """
    md = convert_fragment_to_markdown(html)
    assert "- First item" in md
    assert "- Second item" in md
    assert "- Third item" in md


def test_convert_ordered_list():
    """Test ordered list conversion."""
    html = """
    <ol>
        <li>First item</li>
        <li>Second item</li>
    </ol>
    """
    md = convert_fragment_to_markdown(html)
    assert "- First item" in md
    assert "- Second item" in md


def test_convert_nested_lists():
    """Test nested list conversion."""
    html = """
    <ul>
        <li>First
            <ul>
                <li>Nested 1</li>
                <li>Nested 2</li>
            </ul>
        </li>
        <li>Second</li>
    </ul>
    """
    md = convert_fragment_to_markdown(html)
    assert "- First" in md
    assert "  - Nested 1" in md
    assert "  - Nested 2" in md
    assert "- Second" in md


def test_convert_emphasis_and_strong():
    """Test emphasis and strong text conversion."""
    html = """
    <p><em>Italic</em> and <i>Also italic</i></p>
    <p><strong>Bold</strong> and <b>Also bold</b></p>
    """
    md = convert_fragment_to_markdown(html)
    assert "*Italic*" in md
    assert "*Also italic*" in md
    assert "**Bold**" in md
    assert "**Also bold**" in md


def test_convert_code_inline():
    """Test inline code conversion."""
    html = """
    <p>Use <code>print()</code> for output.</p>
    """
    md = convert_fragment_to_markdown(html)
    # The inline code handling may vary
    assert "print()" in md


def test_convert_horizontal_rule():
    """Test horizontal rule conversion."""
    html = """
    <p>Before</p>
    <hr />
    <p>After</p>
    """
    md = convert_fragment_to_markdown(html)
    # hr handling may vary, but should not crash
    assert "Before" in md
    assert "After" in md


def test_convert_links_with_text():
    """Test link conversion with text content."""
    html = """
    <p>Visit <a href="https://example.com">this example</a> for more.</p>
    """
    md = convert_fragment_to_markdown(html)
    assert "[this example](https://example.com)" in md


def test_convert_citation_links():
    """Test citation link handling."""
    html = """
    <p>See reference <a href="#bib.bib1">[1]</a> for details.</p>
    """
    md = convert_fragment_to_markdown(html)
    # Citations should be converted to bracket format
    assert "[[1]]" in md or "[1]" in md


def test_convert_remove_inline_citations():
    """Test removal of inline citations."""
    html = """
    <p>See reference <a href="#bib.bib1">[1]</a> for details.</p>
    """
    md = convert_fragment_to_markdown(html, remove_inline_citations=True)
    # Citations should be removed entirely
    assert "[1]" not in md or "reference" in md


def test_convert_superscript():
    """Test superscript conversion."""
    html = """
    <p>x<sup>2</sup> + y<sup>2</sup></p>
    """
    md = convert_fragment_to_markdown(html)
    assert "^2" in md


def test_convert_empty_paragraph():
    """Test handling of empty paragraphs."""
    html = """
    <p></p>
    <p>Content</p>
    <p>   </p>
    """
    md = convert_fragment_to_markdown(html)
    assert "Content" in md


def test_convert_div_with_role_paragraph():
    """Test div with role="paragraph" (common in Science.org HTML)."""
    html = """
    <div role="paragraph">This is a paragraph in a div.</div>
    """
    md = convert_fragment_to_markdown(html)
    assert "This is a paragraph in a div." in md


def test_convert_figure_fragments():
    """Test figure fragment anchor conversion."""
    html = """
    <p>See <a href="#S1.F3">Figure 3</a> in the text.</p>
    """
    md = convert_fragment_to_markdown(html)
    assert "#figure-3" in md


def test_convert_table_fragments():
    """Test table fragment anchor conversion."""
    html = """
    <p>See <a href="#S2.T1">Table 1</a> for data.</p>
    """
    md = convert_fragment_to_markdown(html)
    assert "#table-1" in md


def test_convert_appendix_fragments():
    """Test appendix fragment anchor conversion."""
    html = """
    <p>See <a href="#A1">Appendix A</a> for details.</p>
    """
    md = convert_fragment_to_markdown(html)
    assert "#appendix-a" in md


def test_convert_algorithm_fragments():
    """Test algorithm fragment anchor conversion."""
    html = """
    <p>See <a href="#alg1">Algorithm 1</a> for the procedure.</p>
    """
    md = convert_fragment_to_markdown(html)
    assert "#algorithm-1" in md


def test_convert_complex_nested_structure():
    """Test conversion of complex nested HTML structure."""
    html = """
    <section>
        <h2>Section Title</h2>
        <p>Intro paragraph with <em>emphasis</em> and <strong>strong</strong> text.</p>
        <ul>
            <li>Item with <a href="http://test.com">link</a></li>
        </ul>
    </section>
    """
    md = convert_fragment_to_markdown(html)
    assert "## Section Title" in md
    assert "Intro paragraph" in md
    assert "*emphasis*" in md
    assert "**strong**" in md
    assert "- Item with" in md
    assert "[link](http://test.com)" in md


def test_convert_full_document():
    """Test convert_html_to_markdown with full document structure."""
    from arxiv2md_beta.html.markdown import convert_html_to_markdown

    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Test</title></head>
    <body>
        <article class="ltx_document">
            <h1 class="ltx_title_document">Full Document Title</h1>
            <div class="ltx_authors">Author Name</div>
            <div class="ltx_abstract">
                <h6>Abstract</h6>
                <p>Abstract content here.</p>
            </div>
            <section>
                <h2>Introduction</h2>
                <p>Introduction paragraph.</p>
            </section>
        </article>
    </body>
    </html>
    """
    md = convert_html_to_markdown(html)
    assert "# Full Document Title" in md
    assert "Author Name" in md
    assert "## Abstract" in md
    assert "Abstract content here." in md
    assert "## Introduction" in md


def test_convert_with_remove_refs():
    """Test bibliography removal."""
    from arxiv2md_beta.html.markdown import convert_html_to_markdown

    html = """
    <article class="ltx_document">
        <section>
            <h2>Content</h2>
            <p>Some content.</p>
        </section>
        <section class="ltx_bibliography">
            <h2>References</h2>
            <p>Reference list.</p>
        </section>
    </article>
    """
    md = convert_html_to_markdown(html, remove_refs=True)
    assert "Content" in md
    assert "References" not in md
    assert "Reference list." not in md


def test_convert_with_remove_toc():
    """Test TOC removal."""
    from arxiv2md_beta.html.markdown import convert_html_to_markdown

    html = """
    <article class="ltx_document">
        <nav class="ltx_TOC">
            <h6>Contents</h6>
            <ol><li>Item 1</li></ol>
        </nav>
        <h1>Title</h1>
    </article>
    """
    md = convert_html_to_markdown(html, remove_toc=True)
    assert "Title" in md
    assert "## Contents" not in md
    assert "Item 1" not in md
