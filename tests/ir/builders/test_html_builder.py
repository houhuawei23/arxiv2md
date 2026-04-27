"""Tests for HTMLBuilder: HTML → DocumentIR conversion."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir.builders.html import HTMLBuilder


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
        assert "Abstract" in md
        assert "Section 1" in md
        assert "*emphasis*" in md
