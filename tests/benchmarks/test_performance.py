"""Performance benchmarks for arxiv2md-beta.

Run with: pytest tests/benchmarks/ --benchmark-only
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.cache.result_cache import CacheKey
from arxiv2md_beta.query.parser import parse_arxiv_input


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
