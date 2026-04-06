"""Result-level caching for arxiv2md-beta.

Provides caching of final Markdown output and metadata keyed by arXiv ID
and conversion options hash.
"""

from __future__ import annotations

from arxiv2md_beta.cache.result_cache import ResultCache, get_result_cache, reset_result_cache

__all__ = ["ResultCache", "get_result_cache", "reset_result_cache"]
