"""Tests for ImageResolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.ir.resolvers import ImageResolver


class TestImageResolver:
    """Unit tests for the unified image path resolver."""

    def test_empty_resolver_returns_original(self) -> None:
        resolver = ImageResolver()
        assert resolver.resolve("foo.png") == "foo.png"

    def test_exact_path_match(self) -> None:
        resolver = ImageResolver(path_map={"figs/foo.png": Path("/local/foo.png")})
        assert resolver.resolve("figs/foo.png") == "/local/foo.png"

    def test_stem_match(self) -> None:
        resolver = ImageResolver(stem_map={"figure1": Path("/local/fig1.png")})
        assert resolver.resolve("figure1.png") == "/local/fig1.png"

    def test_stem_substring_match(self) -> None:
        resolver = ImageResolver(stem_map={"figure1": Path("/local/fig1.png")})
        # stem appears as substring in src
        assert resolver.resolve("prefix_figure1_suffix.png") == "/local/fig1.png"

    def test_stem_case_insensitive(self) -> None:
        resolver = ImageResolver(stem_map={"Figure1": Path("/local/fig1.png")})
        assert resolver.resolve("figure1.png") == "/local/fig1.png"

    def test_index_match_1_based(self) -> None:
        resolver = ImageResolver(index_map={1: Path("/local/fig1.png")})
        assert resolver.resolve("x1.png", figure_index=1) == "/local/fig1.png"

    def test_index_match_0_based_fallback(self) -> None:
        resolver = ImageResolver(index_map={0: Path("/local/fig0.png")})
        # figure_index=1 should fallback to index 0
        assert resolver.resolve("x1.png", figure_index=1) == "/local/fig0.png"

    def test_path_map_name_match(self) -> None:
        resolver = ImageResolver(path_map={"foo.png": Path("/local/bar.png")})
        assert resolver.resolve("foo.png") == "/local/bar.png"

    def test_path_map_stem_match(self) -> None:
        resolver = ImageResolver(path_map={"foo": Path("/local/bar.png")})
        assert resolver.resolve("foo.jpg") == "/local/bar.png"

    def test_priority_exact_over_stem(self) -> None:
        resolver = ImageResolver(
            path_map={"foo.png": Path("/local/exact.png")},
            stem_map={"foo": Path("/local/stem.png")},
        )
        assert resolver.resolve("foo.png") == "/local/exact.png"

    def test_priority_stem_over_index(self) -> None:
        resolver = ImageResolver(
            stem_map={"foo": Path("/local/stem.png")},
            index_map={1: Path("/local/index.png")},
        )
        assert resolver.resolve("foo.png", figure_index=1) == "/local/stem.png"

    def test_caching(self) -> None:
        resolver = ImageResolver(stem_map={"foo": Path("/local/foo.png")})
        # First call
        r1 = resolver.resolve("foo.png")
        # Second call should hit cache
        r2 = resolver.resolve("foo.png")
        assert r1 == r2 == "/local/foo.png"
        assert len(resolver._cache) == 1

    def test_used_indices_tracking(self) -> None:
        resolver = ImageResolver(index_map={1: Path("/local/fig1.png")})
        resolver.resolve("x.png", figure_index=1)
        assert 1 in resolver._used_indices

    def test_str_values_accepted(self) -> None:
        """Resolver should accept str values (not just Path)."""
        resolver = ImageResolver(
            index_map={0: "/local/fig0.png"},
            stem_map={"foo": "/local/foo.png"},
            path_map={"bar": "/local/bar.png"},
        )
        assert resolver.resolve("x.png", figure_index=1) == "/local/fig0.png"
        assert resolver.resolve("foo.png") == "/local/foo.png"
        assert resolver.resolve("bar") == "/local/bar.png"

    def test_no_match_returns_original(self) -> None:
        resolver = ImageResolver(stem_map={"other": Path("/local/other.png")})
        assert resolver.resolve("unknown.png") == "unknown.png"

    def test_combined_maps(self) -> None:
        """All three map types work together."""
        resolver = ImageResolver(
            index_map={0: Path("/local/by_index.png")},
            stem_map={"stemmed": Path("/local/by_stem.png")},
            path_map={"exact.png": Path("/local/by_exact.png")},
        )
        assert resolver.resolve("exact.png") == "/local/by_exact.png"
        assert resolver.resolve("stemmed.jpg") == "/local/by_stem.png"
        assert resolver.resolve("other.png", figure_index=1) == "/local/by_index.png"
        assert resolver.resolve("none.png") == "none.png"
