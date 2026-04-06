"""Tests for result-level caching."""

from __future__ import annotations

import pytest

from arxiv2md_beta.cache import ResultCache, get_result_cache, reset_result_cache
from arxiv2md_beta.schemas import IngestionResult


@pytest.fixture
def cache(temp_dir) -> ResultCache:
    """Create a fresh result cache for testing."""
    reset_result_cache()
    return ResultCache(cache_dir=temp_dir)


class TestResultCache:
    """Tests for ResultCache functionality."""

    @pytest.mark.asyncio
    async def test_cache_set_and_get(self, cache: ResultCache):
        """Test basic cache set and get operations."""
        result = IngestionResult(
            content="Test markdown content",
            summary="Test summary",
            sections_tree="[]",
        )
        metadata = {"title": "Test Paper", "arxiv_id": "2501.12345"}

        await cache.set(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
            result=result,
            metadata=metadata,
        )

        cached = await cache.get(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
        )

        assert cached is not None
        assert cached.content == "Test markdown content"
        assert cached.metadata["title"] == "Test Paper"

    @pytest.mark.asyncio
    async def test_cache_miss(self, cache: ResultCache):
        """Test cache miss for non-existent entry."""
        cached = await cache.get(
            arxiv_id="2501.00000",
            version=None,
            parser="html",
        )
        assert cached is None

    @pytest.mark.asyncio
    async def test_cache_key_variations(self, cache: ResultCache):
        """Test that different options create different cache keys."""
        result = IngestionResult(
            content="Content",
            summary="Summary",
            sections_tree="[]",
        )
        metadata = {"title": "Paper"}

        # Set with remove_refs=True
        await cache.set(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
            result=result,
            metadata=metadata,
            remove_refs=True,
        )

        # Get with remove_refs=False should miss
        cached = await cache.get(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
            remove_refs=False,
        )
        assert cached is None

        # Get with remove_refs=True should hit
        cached = await cache.get(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
            remove_refs=True,
        )
        assert cached is not None

    @pytest.mark.asyncio
    async def test_cache_invalidation(self, cache: ResultCache):
        """Test cache invalidation."""
        result = IngestionResult(
            content="Content",
            summary="Summary",
            sections_tree="[]",
        )
        metadata = {"title": "Paper"}

        await cache.set(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
            result=result,
            metadata=metadata,
        )

        # Invalidate specific arXiv ID
        removed = await cache.invalidate("2501.12345")
        assert removed == 1

        # Should be a miss now
        cached = await cache.get(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
        )
        assert cached is None

    @pytest.mark.asyncio
    async def test_cache_clear(self, cache: ResultCache):
        """Test clearing all cached results."""
        result = IngestionResult(
            content="Content",
            summary="Summary",
            sections_tree="[]",
        )
        metadata = {"title": "Paper"}

        await cache.set(
            arxiv_id="2501.12345",
            version=None,
            parser="html",
            result=result,
            metadata=metadata,
        )
        await cache.set(
            arxiv_id="2501.12346",
            version=None,
            parser="html",
            result=result,
            metadata=metadata,
        )

        removed = await cache.clear()
        assert removed == 2

    def test_cache_stats(self, cache: ResultCache):
        """Test cache statistics."""
        stats = cache.get_stats()
        assert "entries" in stats
        assert "size_bytes" in stats
        assert "size_mb" in stats

    def test_cache_key_hash_determinism(self):
        """Test that cache keys are deterministic."""
        from arxiv2md_beta.cache.result_cache import CacheKey

        key1 = CacheKey(
            arxiv_id="2501.12345",
            version="v1",
            parser="html",
            remove_refs=True,
            remove_toc=False,
            remove_inline_citations=False,
            section_filter_mode="exclude",
            sections=("Abstract",),
            no_images=False,
        )

        key2 = CacheKey(
            arxiv_id="2501.12345",
            version="v1",
            parser="html",
            remove_refs=True,
            remove_toc=False,
            remove_inline_citations=False,
            section_filter_mode="exclude",
            sections=("Abstract",),
            no_images=False,
        )

        assert key1.to_hash() == key2.to_hash()
