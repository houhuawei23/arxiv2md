"""Tests for HTMLBuilder: HTML → DocumentIR conversion."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir.builders.html import HTMLBuilder
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter


@pytest.fixture
def builder() -> HTMLBuilder:
    return HTMLBuilder()


class TestBasicConversion:
    def test_empty_html(self, builder):
        doc = builder.build("", arxiv_id="test")
        assert doc.metadata.arxiv_id == "test"
        assert doc.metadata.parser == "html"

    def test_title_extraction(self, builder):
        html = """
        <html><body>
        <article class="ltx_document">
        <h1 class="ltx_title_document">Paper Title</h1>
        </article>
        </body></html>"""
        doc = builder.build(html, arxiv_id="test")
        assert doc.metadata.title == "Paper Title"

    def test_abstract_extraction(self, builder):
        html = """
        <html><body>
        <article class="ltx_document">
        <div class="ltx_abstract"><p>Abstract text here.</p></div>
        </article>
        </body></html>"""
        doc = builder.build(html, arxiv_id="test")
        assert len(doc.abstract) > 0

    def test_section_extraction(self, builder):
        html = """
        <html><body>
        <article class="ltx_document">
        <section class="ltx_section">
        <h2 class="ltx_title_section">Introduction</h2>
        <p>Section content.</p>
        </section>
        <section class="ltx_section">
        <h2 class="ltx_title_section">Methods</h2>
        <p>Methods content.</p>
        </section>
        </article>
        </body></html>"""
        doc = builder.build(html, arxiv_id="test")
        assert len(doc.sections) == 2
        assert doc.sections[0].title == "Introduction"
        assert doc.sections[1].title == "Methods"


class TestInlineConversion:
    def test_emphasis(self, builder):
        html = """<p>Text with <em>italic</em> and <strong>bold</strong>.</p>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        # Should have inlines in the paragraph
        assert len(doc.sections) == 1
        assert len(doc.sections[0].blocks) == 1
        para = doc.sections[0].blocks[0]
        assert para.type == "paragraph"

    def test_italic_tags_become_bold(self, builder):
        """HTML em/i/ltx_font_italic must be represented as bold, not italic."""
        html = """
        <p><em>em text</em> and <i>i text</i> and
        <span class="ltx_text ltx_font_italic">class text</span>.</p>
        """
        doc = builder.build(
            f"""<article class='ltx_document'>
            <section class='ltx_section'><h2>T</h2>{html}</section>
            </article>""",
            arxiv_id="test",
        )
        para = doc.sections[0].blocks[0]
        emphases = [il for il in para.inlines if il.type == "emphasis"]
        assert len(emphases) == 3
        for il in emphases:
            assert il.style == "bold"

    def test_link(self, builder):
        html = """<p>Visit <a href="https://example.com">example</a>.</p>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        para = doc.sections[0].blocks[0]
        links = [il for il in para.inlines if hasattr(il, "type") and il.type == "link"]
        assert len(links) == 1
        assert links[0].url == "https://example.com"

    def test_superscript_subscript(self, builder):
        html = """<p>Text<sup>1</sup> and H<sub>2</sub>O.</p>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        para = doc.sections[0].blocks[0]
        types = [il.type for il in para.inlines if hasattr(il, "type")]
        assert "superscript" in types
        assert "subscript" in types


class TestBlockConversion:
    def test_figure(self, builder):
        html = """
        <figure class="ltx_figure">
        <img src="./fig1.png" alt="Figure 1" />
        <figcaption>Figure 1: Overview</figcaption>
        </figure>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        figures = [b for b in doc.sections[0].blocks if b.type == "figure"]
        assert len(figures) == 1
        assert figures[0].figure_id == "figure-1"
        assert len(figures[0].images) == 1

    def test_table(self, builder):
        html = """
        <table>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
        </table>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        tables = [b for b in doc.sections[0].blocks if b.type == "table"]
        assert len(tables) == 1

    def test_list(self, builder):
        html = """
        <ul>
        <li>Item 1</li>
        <li>Item 2</li>
        </ul>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        lists = [b for b in doc.sections[0].blocks if b.type == "list"]
        assert len(lists) == 1
        assert len(lists[0].items) == 2

    def test_blockquote(self, builder):
        html = """<blockquote><p>Some quote</p></blockquote>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        quotes = [b for b in doc.sections[0].blocks if b.type == "blockquote"]
        assert len(quotes) == 1

    def test_code(self, builder):
        html = """<pre><code class="language-python">print(1)</code></pre>"""
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        codes = [b for b in doc.sections[0].blocks if b.type == "code"]
        assert len(codes) == 1
        assert codes[0].text == "print(1)"
        assert codes[0].language == "python"

    def test_ltx_listing_base64(self, builder):
        """ar5iv ``div.ltx_listing`` with embedded data URL becomes a fenced code block."""
        import base64

        payload = "pip install causal-learn"
        b64 = base64.b64encode(payload.encode()).decode()
        html = f"""
        <div class="ltx_listing ltx_lst_language_Python ltx_lst_numbers_left ltx_lstlisting ltx_listing">
        <div class="ltx_listing_data"><a href="data:text/plain;base64,{b64}" download="">⬇</a></div>
        <div class="ltx_listingline"><span class="ltx_tag ltx_tag_listingline">1</span><span>pip</span><span> </span><span>install</span><span> </span><span>causal</span><span>-</span><span>learn</span></div>
        </div>
        """
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        codes = [b for b in doc.sections[0].blocks if b.type == "code"]
        assert len(codes) == 1
        assert codes[0].text == payload
        # Shell commands mis-labelled as Python are reclassified.
        assert codes[0].language == "bash"

    def test_ltx_listing_lines_fallback(self, builder):
        """When no data URL is present, listing lines are joined into a code block."""
        html = """
        <div class="ltx_listing ltx_lst_language_Python">
        <div class="ltx_listingline"><span class="ltx_tag ltx_tag_listingline">1</span><span>cg</span><span> </span><span>=</span><span> </span><span>pc</span><span>(</span><span>data</span><span>)</span></div>
        <div class="ltx_listingline"><span class="ltx_tag ltx_tag_listingline">2</span><span>cg</span><span>.</span><span>draw</span><span>()</span></div>
        </div>
        """
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        codes = [b for b in doc.sections[0].blocks if b.type == "code"]
        assert len(codes) == 1
        assert codes[0].text == "cg = pc(data)\ncg.draw()"
        # Valid Python syntax keeps the declared Python label.
        assert codes[0].language == "python"


class TestAuthorAffiliationParsing:
    """Author and affiliation extraction from arXiv HTML."""

    def test_structured_author_blocks(self, builder):
        """ltx_creator / ltx_role_author with ltx_personname + affiliation."""
        html = """
        <article class="ltx_document">
        <h1 class="ltx_title_document">Test</h1>
        <div class="ltx_authors">
            <span class="ltx_creator ltx_role_author">
                <span class="ltx_personname">Jamie Simon</span>
                <span class="ltx_author_notes">
                    <span class="ltx_text ltx_font_italic">UC Berkeley and Imbue</span>
                </span>
            </span>
            <span class="ltx_creator ltx_role_author">
                <span class="ltx_personname">Daniel Kunin</span>
                <span class="ltx_author_notes">
                    <span class="ltx_text ltx_font_italic">UC Berkeley</span>
                </span>
            </span>
            <span class="ltx_creator ltx_role_author">
                <span class="ltx_personname">Alexander Atanasov</span>
                <span class="ltx_author_notes">
                    <span class="ltx_text ltx_font_italic">Harvard University</span>
                </span>
            </span>
        </div>
        <section class="ltx_section"><h2>Intro</h2><p>Hello.</p></section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        authors = doc.metadata.authors
        assert len(authors) == 3
        assert authors[0].name == "Jamie Simon"
        assert authors[0].affiliations == ["UC Berkeley and Imbue"]
        assert authors[1].name == "Daniel Kunin"
        assert authors[1].affiliations == ["UC Berkeley"]
        assert authors[2].name == "Alexander Atanasov"
        assert authors[2].affiliations == ["Harvard University"]

    def test_sequential_bold_author_spans(self, builder):
        """Flat sequential spans: bold = name, following = affiliation."""
        html = """
        <article class="ltx_document">
        <h1 class="ltx_title_document">Test</h1>
        <div class="ltx_authors">
            <span class="ltx_text ltx_font_bold">Jamie Simon</span>
            <sup>*</sup>
            <span class="ltx_text">UC Berkeley and Imbue</span>
            <span class="ltx_text ltx_font_bold">Daniel Kunin</span>
            <span class="ltx_text">UC Berkeley</span>
            <span class="ltx_text ltx_font_bold">Alexander Atanasov</span>
            <span class="ltx_text">Harvard University</span>
        </div>
        <section class="ltx_section"><h2>Intro</h2><p>Hello.</p></section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        authors = doc.metadata.authors
        assert len(authors) == 3
        assert authors[0].name == "Jamie Simon"
        assert authors[0].affiliations == ["UC Berkeley and Imbue"]
        assert authors[1].name == "Daniel Kunin"
        assert authors[1].affiliations == ["UC Berkeley"]
        assert authors[2].name == "Alexander Atanasov"
        assert authors[2].affiliations == ["Harvard University"]

    def test_ltx_author_personname_only(self, builder):
        """Simple ltx_author / ltx_personname without explicit affiliations."""
        html = """
        <article class="ltx_document">
        <h1 class="ltx_title_document">Test</h1>
        <div class="ltx_authors">
            <span class="ltx_author">
                <span class="ltx_personname">John Doe</span>
            </span>
            <span class="ltx_author">
                <span class="ltx_personname">Jane Smith</span>
            </span>
        </div>
        <section class="ltx_section"><h2>Intro</h2><p>Hello.</p></section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        authors = doc.metadata.authors
        assert len(authors) == 2
        assert authors[0].name == "John Doe"
        assert authors[0].affiliations == []
        assert authors[1].name == "Jane Smith"
        assert authors[1].affiliations == []

    def test_author_names_property(self, builder):
        """PaperMetadata.author_names returns plain name strings."""
        html = """
        <article class="ltx_document">
        <div class="ltx_authors">
            <span class="ltx_creator ltx_role_author">
                <span class="ltx_personname">Alice Foo</span>
                <span class="ltx_author_notes">
                    <span class="ltx_text">MIT</span>
                </span>
            </span>
            <span class="ltx_creator ltx_role_author">
                <span class="ltx_personname">Bob Bar</span>
            </span>
        </div>
        <section class="ltx_section"><h2>Intro</h2><p>Hello.</p></section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        assert doc.metadata.author_names == ["Alice Foo", "Bob Bar"]


class TestEmptySectionRemoval:
    """Leaf sections with no blocks should be dropped."""

    def test_blank_titled_sections_are_removed(self, builder):
        """Headings like Lemma/Proof that carry no content must not appear alone."""
        html = """
        <article class="ltx_document">
        <section class="ltx_section">
            <h2 class="ltx_title_section">Results</h2>
            <p>Some introductory text.</p>
            <div class="ltx_theorem">
                <h6 class="ltx_title ltx_runin ltx_title_theorem">Lemma 3.1 .</h6>
            </div>
            <div class="ltx_proof">
                <h6 class="ltx_title ltx_runin ltx_title_proof">Proof.</h6>
            </div>
            <div class="ltx_theorem">
                <h6 class="ltx_title ltx_runin ltx_title_theorem">Theorem 3.2 .</h6>
                <p>Statement of the theorem.</p>
            </div>
        </section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        # Only the parent "Results" section should survive; empty theorem/proof
        # sections are removed, while a theorem that has content is kept.
        assert len(doc.sections) == 1
        assert doc.sections[0].title == "Results"


class TestRoundTrip:
    """HTML → DocumentIR → Markdown produces valid output."""

    def test_roundtrip(self, builder):
        from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter

        html = """
        <article class="ltx_document">
        <h1 class="ltx_title_document">Test</h1>
        <div class="ltx_abstract"><p>Abstract.</p></div>
        <section class="ltx_section">
        <h2 class="ltx_title_section">Section 1</h2>
        <p>Content with <em>emphasis</em>.</p>
        </section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        emitter = MarkdownEmitter()
        md = emitter.emit(doc)
        import re

        assert "Abstract" in md
        assert "Section 1" in md
        assert "**emphasis**" in md
        # Make sure it is bold, not single-emphasis italic
        assert re.search(r"(?<!\*)\*emphasis\*(?!\*)", md) is None
        assert "*emphasis*" in md


class TestBreakHandling:
    """Line breaks must not leak as raw HTML."""

    def test_block_level_br_is_dropped(self, builder):
        """A ``<br>`` at block level should be ignored, not emitted as raw HTML."""
        html = """
        <article class="ltx_document">
        <section class="ltx_section"><h2>T</h2>
        <p>First.</p>
        <br class="ltx_break"/>
        <p>Second.</p>
        </section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        md = MarkdownEmitter().emit(doc)
        assert "<br" not in md
        assert "First." in md
        assert "Second." in md

    def test_inline_br_becomes_line_break(self, builder):
        """A ``<br>`` inside a paragraph becomes a newline."""
        html = """
        <article class="ltx_document">
        <section class="ltx_section"><h2>T</h2>
        <p>First.<br/>Second.</p>
        </section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        para = doc.sections[0].blocks[0]
        assert any(il.type == "break" for il in para.inlines)


class TestListingLanguage:
    """Code listing language should be validated against content."""

    def test_shell_command_not_marked_python(self, builder):
        """A ``pip install`` listing labelled Python should become bash/text."""
        import base64

        payload = "pip install causal-learn"
        b64 = base64.b64encode(payload.encode()).decode()
        html = f"""
        <div class="ltx_listing ltx_lst_language_Python">
        <div class="ltx_listing_data"><a href="data:text/plain;base64,{b64}" download="">⬇</a></div>
        </div>
        """
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        code = [b for b in doc.sections[0].blocks if b.type == "code"][0]
        assert code.language != "python"

    def test_valid_python_keeps_python_label(self, builder):
        """Valid Python syntax keeps the Python language label."""
        import base64

        payload = "x = 1\nprint(x)"
        b64 = base64.b64encode(payload.encode()).decode()
        html = f"""
        <div class="ltx_listing ltx_lst_language_Python">
        <div class="ltx_listing_data"><a href="data:text/plain;base64,{b64}" download="">⬇</a></div>
        </div>
        """
        doc = builder.build(
            f"<article class='ltx_document'><section class='ltx_section'><h2>T</h2>{html}</section></article>",
            arxiv_id="test",
        )
        code = [b for b in doc.sections[0].blocks if b.type == "code"][0]
        assert code.language == "python"


class TestMathDisplay:
    """Display vs inline math detection."""

    def test_inline_math_is_inline(self, builder):
        html = """
        <article class="ltx_document">
        <section class="ltx_section"><h2>T</h2>
        <p>Let <math alttext="x" display="inline"><annotation encoding="application/x-tex">x</annotation></math> be.</p>
        </section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        para = doc.sections[0].blocks[0]
        math = [il for il in para.inlines if il.type == "math"][0]
        assert not math.display

    def test_block_math_is_display(self, builder):
        html = """
        <article class="ltx_document">
        <section class="ltx_section"><h2>T</h2>
        <p><math alttext="E=mc^2" display="block"><annotation encoding="application/x-tex">E=mc^2</annotation></math></p>
        </section>
        </article>"""
        doc = builder.build(html, arxiv_id="test")
        para = doc.sections[0].blocks[0]
        math = [il for il in para.inlines if il.type == "math"][0]
        assert math.display
