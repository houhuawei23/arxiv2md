"""Performance benchmarks for arxiv2md-beta.

Run with: pytest tests/benchmarks/ --benchmark-only
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.cache.result_cache import CacheKey
from arxiv2md_beta.html.markdown import convert_fragment_to_markdown, convert_html_to_markdown
from arxiv2md_beta.query.parser import parse_arxiv_input


class TestMarkdownConversionBenchmarks:
    """Benchmarks for Markdown conversion performance."""

    def test_benchmark_simple_html(self, benchmark):
        """Benchmark simple HTML to Markdown conversion."""
        html = "<p>Hello <strong>world</strong></p>"
        result = benchmark(convert_fragment_to_markdown, html)
        assert "Hello" in result

    def test_benchmark_complex_document(self, benchmark):
        """Benchmark complex document conversion."""
        html = """
        <article class="ltx_document">
            <h1 class="ltx_title_document">Title</h1>
            <div class="ltx_abstract">
                <p>Abstract paragraph with <em>emphasis</em>.</p>
            </div>
            <section>
                <h2>Section 1</h2>
                <p>Paragraph with <strong>bold</strong> and <a href="http://test.com">link</a>.</p>
                <ul>
                    <li>Item 1</li>
                    <li>Item 2</li>
                    <li>Item 3</li>
                </ul>
            </section>
        </article>
        """
        result = benchmark(convert_html_to_markdown, html)
        assert "Title" in result


class TestQueryParserBenchmarks:
    """Benchmarks for query parsing performance."""

    def test_benchmark_parse_arxiv_id(self, benchmark):
        """Benchmark parsing plain arXiv ID."""
        result = benchmark(parse_arxiv_input, "2501.12345")
        assert result.arxiv_id == "2501.12345"


class TestCacheKeyBenchmarks:
    """Benchmarks for cache key generation."""

    def test_benchmark_cache_key_hash(self, benchmark):
        """Benchmark cache key hash generation."""
        key = CacheKey(
            arxiv_id="2501.12345",
            version="v1",
            parser="html",
            remove_refs=True,
            remove_toc=False,
            remove_inline_citations=False,
            section_filter_mode="exclude",
            sections=("Abstract", "Introduction"),
            no_images=False,
        )
        result = benchmark(key.to_hash)
        assert len(result) == 32


# Benchmark configuration (do not pass timer=... as a string — pytest-benchmark
# expects a callable; a string breaks timer calibration).
pytestmark = pytest.mark.benchmark(
    min_time=0.1,
    max_time=1.0,
    min_rounds=5,
)
