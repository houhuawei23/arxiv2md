"""arXiv ID helpers (single source of truth for version handling)."""

from __future__ import annotations

import re

# Trailing version suffix only: "2501.11120v3" -> "2501.11120",
# "math/0309136v2" -> "math/0309136". Never matches a "v" elsewhere in the ID
# (the old ``split("v")[0]`` truncated IDs like "hep-th/99v..." incorrectly).
_VERSION_SUFFIX_RE = re.compile(r"v\d+$")


def strip_version(arxiv_id: str) -> str:
    """Return *arxiv_id* without a trailing ``vN`` version suffix."""
    return _VERSION_SUFFIX_RE.sub("", arxiv_id.strip())
