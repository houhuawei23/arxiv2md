"""Result-level caching for arxiv2md-beta.

Caches the final Markdown output and metadata keyed by arXiv ID + conversion options hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.aiofiles_compat import async_read_json, async_write_json
from arxiv2md_beta.utils.logging_config import get_logger

if TYPE_CHECKING:
    from arxiv2md_beta.schemas import IngestionResult

logger = get_logger()


@dataclass(frozen=True)
class CacheKey:
    """Cache key components."""

    arxiv_id: str
    version: str | None
    parser: str
    remove_refs: bool
    remove_toc: bool
    remove_inline_citations: bool
    section_filter_mode: str
    sections: tuple[str, ...]
    no_images: bool

    def to_hash(self) -> str:
        """Generate a deterministic hash for this cache key."""
        # Use JSON for consistent serialization
        data = {
            "arxiv_id": self.arxiv_id,
            "version": self.version,
            "parser": self.parser,
            "remove_refs": self.remove_refs,
            "remove_toc": self.remove_toc,
            "remove_inline_citations": self.remove_inline_citations,
            "section_filter_mode": self.section_filter_mode,
            "sections": list(self.sections),
            "no_images": self.no_images,
        }
        json_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:32]


@dataclass
class CachedResult:
    """Cached conversion result."""

    content: str
    metadata: dict
    cache_key: str
    cache_version: str = "1.0"


class ResultCache:
    """Cache for conversion results.

    Results are stored in the cache directory under `results/` subfolder,
    keyed by a hash of the conversion parameters.
    """

    CACHE_VERSION = "1.0"
    CACHE_SUBDIR = "results"

    def __init__(self, cache_dir: Path | None = None) -> None:
        """Initialize the result cache.

        Args:
            cache_dir: Base cache directory. If None, uses settings.
        """
        if cache_dir is None:
            cache_dir = get_settings().resolved_cache_path()
        self.cache_dir = cache_dir / self.CACHE_SUBDIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Result cache initialized at {self.cache_dir}")

    def _get_cache_path(self, key_hash: str) -> Path:
        """Get the cache file path for a key hash."""
        # Use subdirectories to avoid too many files in one directory
        subdir = key_hash[:2]
        return self.cache_dir / subdir / f"{key_hash}.json"

    def _create_cache_key(
        self,
        *,
        arxiv_id: str,
        version: str | None,
        parser: str,
        remove_refs: bool,
        remove_toc: bool,
        remove_inline_citations: bool,
        section_filter_mode: str,
        sections: list[str] | None,
        no_images: bool,
    ) -> CacheKey:
        """Create a cache key from conversion parameters."""
        return CacheKey(
            arxiv_id=arxiv_id,
            version=version,
            parser=parser,
            remove_refs=remove_refs,
            remove_toc=remove_toc,
            remove_inline_citations=remove_inline_citations,
            section_filter_mode=section_filter_mode,
            sections=tuple(sorted(sections or [])),
            no_images=no_images,
        )

    async def get(
        self,
        *,
        arxiv_id: str,
        version: str | None,
        parser: str,
        remove_refs: bool = False,
        remove_toc: bool = False,
        remove_inline_citations: bool = False,
        section_filter_mode: str = "exclude",
        sections: list[str] | None = None,
        no_images: bool = False,
    ) -> CachedResult | None:
        """Get cached result if available.

        Args:
            arxiv_id: arXiv paper ID
            version: Optional version string
            parser: Parser mode ("html" or "latex")
            remove_refs: Whether refs were removed
            remove_toc: Whether TOC was removed
            remove_inline_citations: Whether inline citations were removed
            section_filter_mode: Section filter mode
            sections: List of section filters
            no_images: Whether images were skipped

        Returns:
            CachedResult if found, None otherwise
        """
        key = self._create_cache_key(
            arxiv_id=arxiv_id,
            version=version,
            parser=parser,
            remove_refs=remove_refs,
            remove_toc=remove_toc,
            remove_inline_citations=remove_inline_citations,
            section_filter_mode=section_filter_mode,
            sections=sections,
            no_images=no_images,
        )
        key_hash = key.to_hash()
        cache_path = self._get_cache_path(key_hash)

        if not cache_path.exists():
            logger.debug(f"Cache miss for {arxiv_id} (key: {key_hash})")
            return None

        try:
            data = await async_read_json(cache_path)
            if data.get("cache_version") != self.CACHE_VERSION:
                logger.debug(f"Cache version mismatch for {arxiv_id}")
                return None

            logger.info(f"Cache hit for {arxiv_id} (key: {key_hash})")
            return CachedResult(
                content=data["content"],
                metadata=data["metadata"],
                cache_key=key_hash,
                cache_version=data.get("cache_version", "1.0"),
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Failed to read cache for {arxiv_id}: {e}")
            return None

    async def set(
        self,
        *,
        arxiv_id: str,
        version: str | None,
        parser: str,
        result: "IngestionResult",
        metadata: dict,
        remove_refs: bool = False,
        remove_toc: bool = False,
        remove_inline_citations: bool = False,
        section_filter_mode: str = "exclude",
        sections: list[str] | None = None,
        no_images: bool = False,
    ) -> None:
        """Cache a conversion result.

        Args:
            arxiv_id: arXiv paper ID
            version: Optional version string
            parser: Parser mode ("html" or "latex")
            result: Ingestion result to cache
            metadata: Metadata dictionary
            remove_refs: Whether refs were removed
            remove_toc: Whether TOC was removed
            remove_inline_citations: Whether inline citations were removed
            section_filter_mode: Section filter mode
            sections: List of section filters
            no_images: Whether images were skipped
        """
        key = self._create_cache_key(
            arxiv_id=arxiv_id,
            version=version,
            parser=parser,
            remove_refs=remove_refs,
            remove_toc=remove_toc,
            remove_inline_citations=remove_inline_citations,
            section_filter_mode=section_filter_mode,
            sections=sections,
            no_images=no_images,
        )
        key_hash = key.to_hash()
        cache_path = self._get_cache_path(key_hash)

        # Create subdirectory if needed
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        cache_data = {
            "cache_version": self.CACHE_VERSION,
            "cache_key": key_hash,
            "arxiv_id": arxiv_id,
            "version": version,
            "parser": parser,
            "content": result.content,
            "metadata": metadata,
        }

        try:
            await async_write_json(cache_path, cache_data)
            logger.debug(f"Cached result for {arxiv_id} (key: {key_hash})")
        except OSError as e:
            logger.warning(f"Failed to write cache for {arxiv_id}: {e}")

    async def invalidate(self, arxiv_id: str, version: str | None = None) -> int:
        """Invalidate all cached results for an arXiv ID.

        Args:
            arxiv_id: arXiv paper ID
            version: Optional specific version to invalidate

        Returns:
            Number of cache entries removed
        """
        removed = 0
        for subdir in self.cache_dir.iterdir():
            if not subdir.is_dir():
                continue
            for cache_file in subdir.glob("*.json"):
                try:
                    data = await async_read_json(cache_file)
                    if data.get("arxiv_id") == arxiv_id:
                        if version is None or data.get("version") == version:
                            cache_file.unlink()
                            removed += 1
                            logger.debug(f"Invalidated cache: {cache_file}")
                except (json.JSONDecodeError, OSError):
                    continue
        logger.info(f"Invalidated {removed} cache entries for {arxiv_id}")
        return removed

    async def clear(self) -> int:
        """Clear all cached results.

        Returns:
            Number of cache entries removed
        """
        removed = 0
        for subdir in self.cache_dir.iterdir():
            if not subdir.is_dir():
                continue
            for cache_file in subdir.glob("*.json"):
                try:
                    cache_file.unlink()
                    removed += 1
                except OSError:
                    continue
            # Remove empty subdirectories
            try:
                subdir.rmdir()
            except OSError:
                pass
        logger.info(f"Cleared {removed} cache entries")
        return removed

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        total_files = 0
        total_size = 0

        for subdir in self.cache_dir.iterdir():
            if not subdir.is_dir():
                continue
            for cache_file in subdir.glob("*.json"):
                total_files += 1
                total_size += cache_file.stat().st_size

        return {
            "entries": total_files,
            "size_bytes": total_size,
            "size_mb": round(total_size / (1024 * 1024), 2),
        }


# Module-level singleton
_result_cache: ResultCache | None = None


def get_result_cache() -> ResultCache:
    """Get the global result cache instance."""
    global _result_cache
    if _result_cache is None:
        _result_cache = ResultCache()
    return _result_cache


def reset_result_cache() -> None:
    """Reset the global result cache instance (useful for testing)."""
    global _result_cache
    _result_cache = None
