"""Local configuration for arxiv2md-beta."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_CACHE_DIR = ".arxiv2md_beta_cache"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_FETCH_TIMEOUT_S = 10.0
DEFAULT_FETCH_MAX_RETRIES = 2
DEFAULT_FETCH_BACKOFF_S = 0.5
DEFAULT_USER_AGENT = "arxiv2md-beta/0.1 (+https://github.com/arxiv2md-beta)"

# Local-only cache directory for stored digests and intermediate HTML/TeX.
ARXIV2MD_BETA_CACHE_PATH = Path(
    os.getenv("ARXIV2MD_BETA_CACHE_PATH", DEFAULT_CACHE_DIR)
).expanduser().resolve()
ARXIV2MD_BETA_CACHE_TTL_SECONDS = int(
    os.getenv("ARXIV2MD_BETA_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
)
ARXIV2MD_BETA_FETCH_TIMEOUT_S = float(
    os.getenv("ARXIV2MD_BETA_FETCH_TIMEOUT_S", str(DEFAULT_FETCH_TIMEOUT_S))
)
ARXIV2MD_BETA_FETCH_MAX_RETRIES = int(
    os.getenv("ARXIV2MD_BETA_FETCH_MAX_RETRIES", str(DEFAULT_FETCH_MAX_RETRIES))
)
ARXIV2MD_BETA_FETCH_BACKOFF_S = float(
    os.getenv("ARXIV2MD_BETA_FETCH_BACKOFF_S", str(DEFAULT_FETCH_BACKOFF_S))
)
ARXIV2MD_BETA_USER_AGENT = os.getenv(
    "ARXIV2MD_BETA_USER_AGENT", DEFAULT_USER_AGENT
)
