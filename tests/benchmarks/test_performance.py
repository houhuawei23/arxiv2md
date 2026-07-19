"""Performance benchmarks for arxiv2md-beta.

Run with: pytest tests/benchmarks/ --benchmark-only
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.query.parser import parse_arxiv_input


class TestQueryParserBenchmarks:
    """Benchmarks for query parsing performance."""

    def test_benchmark_parse_arxiv_id(self, benchmark):
        """Benchmark parsing plain arXiv ID."""
        result = benchmark(parse_arxiv_input, "2501.12345")
        assert result.arxiv_id == "2501.12345"


# Benchmark configuration (do not pass timer=... as a string — pytest-benchmark
# expects a callable; a string breaks timer calibration).
pytestmark = pytest.mark.benchmark(
    min_time=0.1,
    max_time=1.0,
    min_rounds=5,
)
